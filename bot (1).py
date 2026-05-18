"""
@Taklifnomachi_online_bot — Taklifnomachi.online rasmiy boti
Ro'yxatdan o'tish, to'lov tasdiqlash, profil, savollar, admin panel
"""
import asyncio
import logging
import os
from datetime import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

from config import BOT_TOKEN, ADMIN_IDS, SITE_URL, PAYMENT_GROUP_ID, WEBHOOK_SECRET, PORT
from database import (
    init_db, get_or_create_user, get_user_by_code, get_user_by_tg,
    add_payment, confirm_payment, reject_payment, get_user_payments,
    get_stats, get_all_user_ids, save_broadcast, register_site
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class Reg(StatesGroup):
    login = State()
    password = State()

class Pay(StatesGroup):
    amount = State()
    screenshot = State()

class Broadcast(StatesGroup):
    text = State()


def is_admin(uid): return uid in ADMIN_IDS


# ==================== KEYBOARDS ====================
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="💳 To'lov tasdiqlash")],
        [KeyboardButton(text="👤 Profilim"), KeyboardButton(text="❓ Savollar")],
        [KeyboardButton(text="🌐 Saytga o'tish")],
    ], resize_keyboard=True)

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="adm_stats")],
        [InlineKeyboardButton(text="📨 Ommaviy xabar", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="⏳ Kutilayotgan to'lovlar", callback_data="adm_pending")],
    ])

def payment_kb(pid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"pay_ok_{pid}"),
         InlineKeyboardButton(text="❌ Rad etish", callback_data=f"pay_no_{pid}")],
    ])

def site_btn():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Saytni ochish", url=SITE_URL)]
    ])


# ==================== /start ====================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

    text = (
        f"👋 Assalomu alaykum, <b>{message.from_user.first_name}</b>!\n\n"
        f"🎉 <b>Taklifnomachi.online</b> — O'zbekistonda birinchi raqamli taklifnomalar platformasi.\n\n"
        f"📨 To'y, tug'ilgan kun, tadbir va dil izhorlari uchun chiroyli taklifnomalar yarating va ulashing!\n\n"
        f"🔑 Sizning shaxsiy kodingiz: <code>{user['payment_code']}</code>\n"
        f"<i>(bu kod barcha to'lovlarda ishlatiladi)</i>\n\n"
        f"Quyidagi tugmalar orqali davom eting 👇"
    )
    await message.answer(text, reply_markup=main_kb(), parse_mode="HTML")


# ==================== TO'LOV TASDIQLASH ====================
@dp.message(F.text == "💳 To'lov tasdiqlash")
async def start_payment(message: Message, state: FSMContext):
    user = await get_user_by_tg(message.from_user.id)
    if not user:
        await message.answer("Avval /start bosing!")
        return
    await message.answer(
        f"💰 To'lov summasini kiriting (so'mda):\n\n"
        f"<i>Masalan: 15000</i>\n\n"
        f"🔑 Sizning kodingiz: <code>{user['payment_code']}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor", callback_data="cancel")]
        ])
    )
    await state.set_state(Pay.amount)

@dp.message(Pay.amount)
async def pay_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.replace(" ", "").replace(",", ""))
        if amount < 1000:
            await message.answer("⚠️ Minimal summa: 1,000 so'm")
            return
    except:
        await message.answer("⚠️ Faqat raqam kiriting. Masalan: 15000")
        return

    await state.update_data(amount=amount)
    await message.answer(
        f"💰 Summa: <b>{amount:,} so'm</b>\n\n"
        f"📸 Endi to'lov screenshotini yuboring:",
        parse_mode="HTML"
    )
    await state.set_state(Pay.screenshot)

@dp.message(Pay.screenshot, F.photo)
async def pay_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount", 0)
    user = await get_user_by_tg(message.from_user.id)
    photo = message.photo[-1]

    payment = await add_payment(message.from_user.id, user['payment_code'], amount, photo.file_id)

    await message.answer(
        "✅ Screenshot qabul qilindi!\n\n"
        "⏳ Admin tekshiruvini kuting.\n"
        "Natija tez orada xabar qilinadi.",
        reply_markup=main_kb()
    )
    await state.clear()

    # Guruhga yuborish
    if PAYMENT_GROUP_ID:
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        caption = (
            f"💳 <b>Yangi to'lov #{payment['id']}</b>\n\n"
            f"👤 {message.from_user.first_name} (@{message.from_user.username or '—'})\n"
            f"🔑 Kod: <code>{user['payment_code']}</code>\n"
            f"💰 Summa: <b>{amount:,} so'm</b>\n"
            f"📅 Sana: {now}\n"
        )
        try:
            await bot.send_photo(PAYMENT_GROUP_ID, photo=photo.file_id, caption=caption, reply_markup=payment_kb(payment['id']), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Guruhga yuborishda xato: {e}")
            # Adminlarga alohida yuborish
            for aid in ADMIN_IDS:
                try:
                    await bot.send_photo(aid, photo=photo.file_id, caption=caption, reply_markup=payment_kb(payment['id']), parse_mode="HTML")
                except: pass

@dp.message(Pay.screenshot)
async def pay_not_photo(message: Message):
    await message.answer("📸 Iltimos, <b>rasm</b> (screenshot) yuboring!", parse_mode="HTML")


# ==================== ADMIN: TASDIQLASH/RAD ETISH ====================
@dp.callback_query(F.data.startswith("pay_ok_"))
async def admin_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Ruxsat yo'q!"); return
    pid = int(cb.data.split("_")[2])
    payment = await confirm_payment(pid, cb.from_user.id)
    if payment:
        # Foydalanuvchiga xabar
        try:
            await bot.send_message(payment['telegram_id'],
                f"✅ <b>To'lovingiz tasdiqlandi!</b>\n\n💰 {payment['amount']:,} so'm\n\nRahmat! Saytda davom eting 👇",
                parse_mode="HTML", reply_markup=site_btn()
            )
        except: pass
        await cb.message.edit_caption(caption=(cb.message.caption or "") + "\n\n✅ <b>TASDIQLANDI</b>", parse_mode="HTML")
    await cb.answer("✅ Tasdiqlandi!")

@dp.callback_query(F.data.startswith("pay_no_"))
async def admin_reject(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Ruxsat yo'q!"); return
    pid = int(cb.data.split("_")[2])
    await reject_payment(pid, cb.from_user.id)
    await cb.message.edit_caption(caption=(cb.message.caption or "") + "\n\n❌ <b>RAD ETILDI</b>", parse_mode="HTML")
    await cb.answer("❌ Rad etildi!")


# ==================== PROFILIM ====================
@dp.message(F.text == "👤 Profilim")
async def my_profile(message: Message):
    user = await get_user_by_tg(message.from_user.id)
    if not user:
        await message.answer("Avval /start bosing!"); return

    payments = await get_user_payments(message.from_user.id)
    total = len(payments)
    confirmed = sum(1 for p in payments if p['status'] == 'confirmed')
    pending = sum(1 for p in payments if p['status'] == 'pending')
    total_sum = sum(p['amount'] for p in payments if p['status'] == 'confirmed')

    text = (
        f"👤 <b>Profilim</b>\n\n"
        f"📛 Ism: {user['first_name'] or '—'}\n"
        f"🆔 Username: @{user['username'] or '—'}\n"
        f"🔑 Shaxsiy kod: <code>{user['payment_code']}</code>\n"
        f"🌐 Sayt login: {user['site_login'] or 'Ro\\'yxatdan o\\'tilmagan'}\n\n"
        f"📊 <b>To'lovlar statistikasi:</b>\n"
        f"  💳 Jami: {total}\n"
        f"  ✅ Tasdiqlangan: {confirmed}\n"
        f"  ⏳ Kutilmoqda: {pending}\n"
        f"  💰 Umumiy: {total_sum:,} so'm\n\n"
        f"📅 Ro'yxatdan o'tgan: {user['created_at'].strftime('%d.%m.%Y')}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Saytda profilim", url=f"{SITE_URL}/profile")],
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="refresh_profile")],
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "refresh_profile")
async def refresh_profile(cb: CallbackQuery):
    await cb.answer("Yangilandi!")
    # Profil xabarini qayta yuborish
    await my_profile(cb.message)


# ==================== SAVOLLAR (Auto chatbot) ====================
FAQ_DATA = {
    "taklifnoma": "📨 <b>Taklifnoma qanday tayyorlanadi?</b>\n\n1️⃣ Saytda shablon tanlang\n2️⃣ Ma'lumotlarni kiriting (ism, sana, manzil)\n3️⃣ Link olasiz\n4️⃣ Do'stlaringizga yuboring\n5️⃣ Javoblarni profildan kuzating",
    "tolov": "💳 <b>Qanday to'lov qilaman?</b>\n\n1️⃣ Premium shablon tanlang\n2️⃣ Shu botda '💳 To'lov tasdiqlash' bosing\n3️⃣ Summani kiriting\n4️⃣ Screenshot yuboring\n5️⃣ Admin tasdiqlaydi — link tayyor!",
    "maxsus": "💎 <b>Maxsus taklifnoma nima?</b>\n\nShaxsiy talablaringiz asosida alohida domen va profillik sayt yaratib beriladi.\n\n💰 Narxi: 159,000 so'mdan\n📩 Buyurtma: @ndd_admin",
    "tasdiq": "⏳ <b>To'lov tasdiqlanmasa?</b>\n\nAdmin 10 daqiqa ichida tekshiradi.\nAgar kechiksa:\n📞 Admin: @ndd_admin",
    "kod": "🔑 <b>Shaxsiy kod nima?</b>\n\nBu sizning doimiy to'lov kodingiz. Har safar to'lov qilganingizda shu kod ishlatiladi.\n\nKodingizni bilish uchun 👤 Profilim tugmasini bosing.",
}

@dp.message(F.text == "❓ Savollar")
async def faq_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Taklifnoma qanday tayyorlanadi?", callback_data="faq_taklifnoma")],
        [InlineKeyboardButton(text="💳 Qanday to'lov qilaman?", callback_data="faq_tolov")],
        [InlineKeyboardButton(text="💎 Maxsus taklifnoma nima?", callback_data="faq_maxsus")],
        [InlineKeyboardButton(text="⏳ To'lov tasdiqlanmasa?", callback_data="faq_tasdiq")],
        [InlineKeyboardButton(text="🔑 Shaxsiy kod nima?", callback_data="faq_kod")],
        [InlineKeyboardButton(text="📞 Admin bilan bog'lanish", url="https://t.me/ndd_admin")],
    ])
    await message.answer("❓ <b>Savollar</b>\n\nQuyidagilardan birini tanlang:", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data.startswith("faq_"))
async def faq_answer(cb: CallbackQuery):
    key = cb.data.replace("faq_", "")
    text = FAQ_DATA.get(key, "Ma'lumot topilmadi.")
    text += "\n\n—\n<i>Qo'shimcha savollar uchun: @ndd_admin</i>"
    await cb.message.answer(text, parse_mode="HTML")
    await cb.answer()


# ==================== SAYTGA O'TISH ====================
@dp.message(F.text == "🌐 Saytga o'tish")
async def go_site(message: Message):
    await message.answer(f"🌐 <b>Taklifnomachi.online</b>\n\nSaytga o'tish uchun tugmani bosing 👇", parse_mode="HTML", reply_markup=site_btn())


# ==================== ADMIN ====================
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ruxsat yo'q!"); return
    await message.answer("🛠 <b>Admin paneli</b>", parse_mode="HTML", reply_markup=admin_kb())

@dp.callback_query(F.data == "adm_stats")
async def adm_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    s = await get_stats()
    text = (
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: {s['users']}\n"
        f"💳 Jami to'lovlar: {s['payments']}\n"
        f"✅ Tasdiqlangan: {s['confirmed']}\n"
        f"⏳ Kutilmoqda: {s['pending']}\n"
        f"💰 Daromad: {s['revenue']:,} so'm"
    )
    await cb.message.answer(text, parse_mode="HTML")
    await cb.answer()

@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    await cb.message.answer("📨 Ommaviy xabarni kiriting:\n\n<i>HTML format qo'llab-quvvatlanadi</i>", parse_mode="HTML")
    await state.set_state(Broadcast.text)
    await cb.answer()

@dp.message(Broadcast.text)
async def adm_broadcast_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear(); return
    text = message.text or message.caption or ""
    await state.clear()

    users = await get_all_user_ids()
    success = 0; fail = 0
    for uid in users:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            success += 1
        except:
            fail += 1
        await asyncio.sleep(0.05)

    await save_broadcast(message.from_user.id, text, len(users), success, fail)
    await message.answer(f"📨 <b>Yuborildi!</b>\n\n✅ {success} ta muvaffaqiyatli\n❌ {fail} ta yuborilmadi\n📊 Jami: {len(users)}", parse_mode="HTML")

@dp.callback_query(F.data == "adm_pending")
async def adm_pending(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM bot_payments WHERE status='pending' ORDER BY created_at DESC LIMIT 10")
    if not rows:
        await cb.message.answer("✅ Kutilayotgan to'lovlar yo'q!")
        await cb.answer(); return
    for p in rows:
        user = await get_user_by_tg(p['telegram_id'])
        uname = user['username'] if user else '—'
        text = f"💳 #{p['id']} | @{uname}\n💰 {p['amount']:,} so'm | 🔑 {p['payment_code']}\n📅 {p['created_at'].strftime('%d.%m %H:%M')}"
        if p['screenshot_file_id']:
            await bot.send_photo(cb.message.chat.id, photo=p['screenshot_file_id'], caption=text, reply_markup=payment_kb(p['id']), parse_mode="HTML")
        else:
            await cb.message.answer(text, reply_markup=payment_kb(p['id']), parse_mode="HTML")
    await cb.answer()


# ==================== CANCEL ====================
@dp.callback_query(F.data == "cancel")
async def cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Bekor qilindi.")
    await cb.answer()


# ==================== UNKNOWN MESSAGES (auto chatbot) ====================
@dp.message()
async def auto_reply(message: Message):
    text = (message.text or "").lower()

    # Oddiy auto-javoblar
    keywords = {
        "salom": "👋 Salom! Qanday yordam bera olaman?",
        "rahmat": "😊 Arzimaydi! Yana savollar bo'lsa yozing.",
        "narx": "💰 Bepul shablonlar bor! Premium shablonlar 15,000 so'mdan.\n💎 Maxsus taklifnoma: 159,000 so'mdan.\n\nBatafsil: @ndd_admin",
        "tolov": FAQ_DATA["tolov"],
        "to'lov": FAQ_DATA["tolov"],
        "taklifnoma": FAQ_DATA["taklifnoma"],
        "maxsus": FAQ_DATA["maxsus"],
        "kod": FAQ_DATA["kod"],
        "yordam": "❓ Tugmalardan foydalaning yoki @ndd_admin ga yozing.",
        "admin": "📞 Admin: @ndd_admin",
    }

    for kw, reply in keywords.items():
        if kw in text:
            await message.answer(reply + "\n\n—\n<i>Batafsil: @ndd_admin</i>", parse_mode="HTML")
            return

    await message.answer(
        "🤔 Tushunmadim.\n\nQuyidagi tugmalardan foydalaning yoki ❓ Savollar bo'limiga qarang.\n\n📞 Qo'shimcha: @ndd_admin",
        reply_markup=main_kb()
    )


# ==================== WEBHOOK + HEALTH ====================
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"

async def health(request):
    return web.json_response({"status": "ok", "bot": "@Taklifnomachi_online_bot"})

async def on_startup(app):
    await init_db()
    url = RENDER_URL or os.getenv("RENDER_URL", "")
    if url:
        wh = f"{url}{WEBHOOK_PATH}"
        await bot.set_webhook(wh, drop_pending_updates=True)
        logger.info(f"Webhook: {wh}")
    else:
        logger.error("RENDER_EXTERNAL_URL yo'q!")

def main():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    app.on_startup.append(on_startup)
    logger.info(f"Starting on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
