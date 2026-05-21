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
                language VARCHAR(5) DEFAULT 'uz',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_payment_logs (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                code VARCHAR(10),
                amount BIGINT DEFAULT 0,
                screenshot_file_id TEXT,
                status VARCHAR(20) DEFAULT 'auto_confirmed',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_broadcasts (
                id SERIAL PRIMARY KEY,
                admin_id BIGINT, message_text TEXT,
                total INT DEFAULT 0, success INT DEFAULT 0, fail INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key VARCHAR(100) PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # Default karta qo'shish (agar yo'q bo'lsa)
        exists = await conn.fetchval("SELECT 1 FROM bot_settings WHERE key='payment_card'")
        if not exists:
            await conn.execute("INSERT INTO bot_settings(key,value) VALUES('payment_card','{}')") 
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
            await conn.execute("INSERT INTO bot_users(telegram_id,username,first_name,payment_code) VALUES($1,$2,$3,$4)", tg_id, username, first_name, code)
            user = await conn.fetchrow("SELECT * FROM bot_users WHERE telegram_id=$1", tg_id)
        else:
            await conn.execute("UPDATE bot_users SET username=$1,first_name=$2 WHERE telegram_id=$3", username, first_name, tg_id)
        return user

async def get_user_by_tg(tg_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM bot_users WHERE telegram_id=$1", tg_id)

async def save_payment_log(tg_id, code, amount, screenshot_file_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_payment_logs(telegram_id,code,amount,screenshot_file_id) VALUES($1,$2,$3,$4)", tg_id, code, amount, screenshot_file_id)

async def get_user_payments(tg_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM bot_payment_logs WHERE telegram_id=$1 ORDER BY created_at DESC LIMIT 20", tg_id)

async def get_stats():
    pool = await get_pool()
    async with pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM bot_users")
        payments = await conn.fetchval("SELECT COUNT(*) FROM bot_payment_logs")
        paid = await conn.fetchval("SELECT COUNT(*) FROM bot_payment_logs WHERE status='auto_confirmed'")
        revenue = await conn.fetchval("SELECT COALESCE(SUM(amount),0) FROM bot_payment_logs WHERE status='auto_confirmed'")
        return {"users":users, "payments":payments, "paid":paid, "revenue":revenue}

async def get_all_user_ids():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_id FROM bot_users")
        return [r['telegram_id'] for r in rows]

async def save_broadcast(admin_id, text, total, success, fail):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_broadcasts(admin_id,message_text,total,success,fail) VALUES($1,$2,$3,$4,$5)", admin_id, text, total, success, fail)


async def get_setting(key):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM bot_settings WHERE key=$1", key)
        return row['value'] if row else None

async def set_setting(key, value):
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM bot_settings WHERE key=$1", key)
        if exists:
            await conn.execute("UPDATE bot_settings SET value=$1, updated_at=NOW() WHERE key=$2", value, key)
        else:
            await conn.execute("INSERT INTO bot_settings(key,value) VALUES($1,$2)", key, value)

async def get_payment_card():
    """To'lov kartasini olish"""
    import json
    raw = await get_setting('payment_card')
    if raw:
        try: return json.loads(raw)
        except: pass
    return None

async def set_payment_card(card_number, owner_name, card_type='HUMO'):
    """To'lov kartasini saqlash"""
    import json
    data = json.dumps({"number": card_number, "owner": owner_name, "type": card_type})
    await set_setting('payment_card', data)
