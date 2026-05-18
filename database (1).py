import asyncpg
import os
import random

_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"), min_size=1, max_size=5)
    return _pool

async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username VARCHAR(255),
                first_name VARCHAR(255),
                payment_code VARCHAR(6) UNIQUE,
                site_login VARCHAR(100),
                site_user_id INT,
                language VARCHAR(5) DEFAULT 'uz',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_payments (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                payment_code VARCHAR(6),
                amount BIGINT,
                screenshot_file_id TEXT,
                status VARCHAR(20) DEFAULT 'pending',
                admin_id BIGINT,
                created_at TIMESTAMP DEFAULT NOW(),
                confirmed_at TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_broadcasts (
                id SERIAL PRIMARY KEY,
                admin_id BIGINT,
                message_text TEXT,
                total INT DEFAULT 0,
                success INT DEFAULT 0,
                fail INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
    print("DB initialized!")

def gen_code():
    return str(random.randint(100000, 999999))

async def get_or_create_user(tg_id, username=None, first_name=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM bot_users WHERE telegram_id=$1", tg_id)
        if not user:
            code = gen_code()
            while await conn.fetchval("SELECT 1 FROM bot_users WHERE payment_code=$1", code):
                code = gen_code()
            await conn.execute(
                "INSERT INTO bot_users(telegram_id,username,first_name,payment_code) VALUES($1,$2,$3,$4)",
                tg_id, username, first_name, code
            )
            user = await conn.fetchrow("SELECT * FROM bot_users WHERE telegram_id=$1", tg_id)
        else:
            await conn.execute("UPDATE bot_users SET username=$1,first_name=$2 WHERE telegram_id=$3", username, first_name, tg_id)
        return user

async def register_site(tg_id, login, password_hash, site_user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE bot_users SET site_login=$1, site_user_id=$2 WHERE telegram_id=$3", login, site_user_id, tg_id)

async def get_user_by_code(code):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM bot_users WHERE payment_code=$1", code)

async def get_user_by_tg(tg_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM bot_users WHERE telegram_id=$1", tg_id)

async def add_payment(tg_id, code, amount, screenshot_file_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "INSERT INTO bot_payments(telegram_id,payment_code,amount,screenshot_file_id) VALUES($1,$2,$3,$4) RETURNING *",
            tg_id, code, amount, screenshot_file_id
        )

async def confirm_payment(payment_id, admin_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE bot_payments SET status='confirmed',admin_id=$1,confirmed_at=NOW() WHERE id=$2", admin_id, payment_id)
        return await conn.fetchrow("SELECT * FROM bot_payments WHERE id=$1", payment_id)

async def reject_payment(payment_id, admin_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE bot_payments SET status='rejected',admin_id=$1 WHERE id=$2", admin_id, payment_id)

async def get_user_payments(tg_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM bot_payments WHERE telegram_id=$1 ORDER BY created_at DESC LIMIT 20", tg_id)

async def get_stats():
    pool = await get_pool()
    async with pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM bot_users")
        payments = await conn.fetchval("SELECT COUNT(*) FROM bot_payments")
        confirmed = await conn.fetchval("SELECT COUNT(*) FROM bot_payments WHERE status='confirmed'")
        pending = await conn.fetchval("SELECT COUNT(*) FROM bot_payments WHERE status='pending'")
        revenue = await conn.fetchval("SELECT COALESCE(SUM(amount),0) FROM bot_payments WHERE status='confirmed'")
        return {"users":users,"payments":payments,"confirmed":confirmed,"pending":pending,"revenue":revenue}

async def get_all_user_ids():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_id FROM bot_users")
        return [r['telegram_id'] for r in rows]

async def save_broadcast(admin_id, text, total, success, fail):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_broadcasts(admin_id,message_text,total,success,fail) VALUES($1,$2,$3,$4,$5)", admin_id, text, total, success, fail)
