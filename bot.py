"""
@Taklifnomachi_online_bot — Taklifnomachi.online rasmiy boti
Sayt kodi orqali to'lov tasdiqlash (avto-tasdiq), profil, savollar
"""
import asyncio
import logging
import os
import json
from datetime import datetime

import aiohttp
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
    init_db, get_or_create_user, get_user_by_tg, get_user_payments,
    get_stats, get_all_user_ids, save_broadcast, save_payment_log,
    get_payment_card, set_payment_card
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Sayt API bilan aloqa
SITE_API = SITE_URL.rstrip('/')

async def api_check_payment(code):
    """Saytdan to'lov ma'lumotlarini olish"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{SITE_API}/api/payments/check/{code}") as r:
                if r.status == 200:
                    return await r.json()
                return None
    except Exception as e:
        logger.error(f"API check error: {e}")
        return None

async def api_verify_payment(code, telegram_id):
    """Saytga to'lov tasdiqlandi deb xabar berish"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{SITE_API}/api/payments/verify", json={"code": code, "telegramId": str(telegram_id)}) as r:
                if r.status == 200:
                    return await r.json()
                return None
    except Exception as e:
        logger.error(f"API verify error: {e}")
        return None


class Pay(StatesGroup):
    code = State()
    screenshot = State()

class Broadcast(StatesGroup):
    text = State()

class CardSetup(StatesGroup):
    number = State()
    owner = State()
    card_type = State()


def is_admin(uid): return uid in ADMIN_IDS

def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="💳 To'lov tasdiqlash")],
        [KeyboardButton(text="👤 Profilim"), KeyboardButton(text="❓ Savollar")],
        [KeyboardButton(text="🌐 Saytga o'tish")],
    ], resize_keyboard=True)

def site_btn():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Saytni ochish", url=SITE_URL)],
        [InlineKeyboardButton(text="🔄 Saytni yangilang", url=SITE_URL + "/profile")]
    ])


# ==================== /start ====================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

    # Deep link: /start pay_123456
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("pay_"):
        code = args[1].replace("pay_", "")
        data = await api_check_payment(code)
        if data and data.get('payment'):
            pay = data['payment']
            cat_names = {'wedding': "💍 To'y", 'birthday': "🎂 Tug'ilgan kun", 'love': "❤️ Dil izhori", 'event': "🎤 Tadbir"}
            inv_data = pay.get('data', {})
            if isinstance(inv_data, str):
                try: inv_data = json.loads(inv_data)
                except: inv_data = {}

            card = await get_payment_card()
            card_info = ""
            if card:
                fmt = card['number'].replace(' ','')
                fmt = ' '.join([fmt[i:i+4] for i in range(0, len(fmt), 4)])
                card_info = f"\n💳 Karta: <code>{fmt}</code>\n👤 {card['owner']} ({card.get('type','HUMO')})\n"
            text = (
                f"💳 <b>To'lov ma'lumotlari</b>\n\n"
                f"📌 Tur: {cat_names.get(pay['category'], pay['category'])}\n"
                f"💰 Summa: <b>{pay['amount']:,} so'm</b>\n"
                f"🔑 Kod: <code>{code}</code>"
                f"{card_info}\n"
                f"📸 To'lov qilganingizdan so'ng <b>screenshot</b> yuboring!"
            )
            await message.answer(text, parse_mode="HTML")
            await state.update_data(pay_code=code, amount=pay['amount'])
            await state.set_state(Pay.screenshot)
            return

    text = (
        f"👋 Assalomu alaykum, <b>{message.from_user.first_name}</b>!\n\n"
        f"🎉 <b>Taklifnomachi.online</b> — raqamli taklifnomalar platformasi.\n\n"
        f"📨 To'y, tug'ilgan kun, tadbir va dil izhorlari uchun chiroyli taklifnomalar yarating!\n\n"
        f"💳 Premium taklifnoma olish uchun saytdan olgan <b>6 raqamli kodni</b> shu botga yuboring.\n\n"
        f"Quyidagi tugmalar orqali davom eting 👇"
    )
    await message.answer(text, reply_markup=main_kb(), parse_mode="HTML")


# ==================== TO'LOV TASDIQLASH ====================
@dp.message(F.text == "💳 To'lov tasdiqlash")
async def start_payment(message: Message, state: FSMContext):
    await message.answer(
        "🔑 Saytdan olgan <b>6 raqamli kodni</b> kiriting:\n\n"
        "<i>Bu kod saytda premium shablon tanlaganda beriladi</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor", callback_data="cancel")]
        ])
    )
    await state.set_state(Pay.code)


@dp.message(Pay.code)
async def process_code(message: Message, state: FSMContext):
    code = message.text.strip()
    if not code.isdigit() or len(code) != 6:
        await message.answer("⚠️ Kod 6 ta raqamdan iborat. Qayta kiriting:")
        return

    # Saytdan tekshirish
    data = await api_check_payment(code)
    if not data or not data.get('payment'):
        await message.answer(
            "❌ Bu kod bo'yicha to'lov topilmadi.\n\n"
            "Tekshiring:\n"
            "• Kodni to'g'ri yozdingizmi?\n"
            "• Saytda premium shablon tanladingizmi?\n\n"
            "Muammo bo'lsa: @ndd_admin",
            reply_markup=main_kb()
        )
        await state.clear()
        return

    pay = data['payment']
    if pay['status'] == 'paid':
        await message.answer(
            f"✅ Bu to'lov allaqachon tasdiqlangan!\n\n"
            f"🔗 Linkingiz: {pay.get('link', 'Saytda koring')}\n\n"
            f"Saytda profilingizni oching 👇",
            parse_mode="HTML", reply_markup=site_btn()
        )
        await state.clear()
        return

    cat_names = {'wedding': "💍 To'y", 'birthday': "🎂 Tug'ilgan kun", 'love': "❤️ Dil izhori", 'event': "🎤 Tadbir"}

    # To'lov kartasini olish
    card = await get_payment_card()
    card_text = ""
    if card:
        fmt_num = card['number'].replace(' ','')
        fmt_num = ' '.join([fmt_num[i:i+4] for i in range(0, len(fmt_num), 4)])
        card_text = (
            f"\n💳 <b>To'lov kartasi:</b>\n"
            f"┌─────────────────────┐\n"
            f"│ <code>{fmt_num}</code>\n"
            f"│ {card['owner']}\n"
            f"│ {card.get('type','HUMO')}\n"
            f"└─────────────────────┘\n"
        )
    else:
        card_text = "\n⚠️ Admin hali to'lov kartasini qo'shmagan.\n"

    text = (
        f"✅ <b>To'lov topildi!</b>\n\n"
        f"📌 Tur: {cat_names.get(pay['category'], pay['category'])}\n"
        f"📋 Shablon: {pay.get('template_name', '')}\n"
        f"💰 Summa: <b>{pay['amount']:,} so'm</b>\n"
        f"{card_text}\n"
        f"To'lov qilganingizdan so'ng <b>screenshot</b> yuboring 📸"
    )
    await message.answer(text, parse_mode="HTML")
    await state.update_data(pay_code=code, amount=pay['amount'])
    await state.set_state(Pay.screenshot)


@dp.message(Pay.screenshot, F.photo)
async def process_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    code = data.get('pay_code')
    amount = data.get('amount', 0)

    if not code:
        await message.answer("⚠️ Xatolik. Qaytadan /start bosing.", reply_markup=main_kb())
        await state.clear()
        return

    photo = message.photo[-1]

    # ============ AVTO-TASDIQLASH ============
    # Saytga to'lov tasdiqlandi deb xabar beramiz
    result = await api_verify_payment(code, message.from_user.id)

    if result and result.get('success'):
        # Foydalanuvchiga xabar — to'lov tasdiqlandi, endi saytda domen tanlaydi
        text = (
            f"✅ <b>To'lovingiz tasdiqlandi!</b>\n\n"
            f"💰 Summa: {amount:,} so'm\n"
            f"🔑 Kodingiz: <code>{code}</code>\n"
            f"<i>(bu kod sizning doimiy raqamingiz — saqlang!)</i>\n\n"
            f"🌐 <b>Endi saytga o'ting va o'zingizga maxsus domen nom tanlang!</b>\n\n"
            f"Masalan: <i>ali-zarina</i>, <i>mylove</i>, <i>wedding2026</i>\n\n"
            f"📱 Saytda profilingizni oching va taklifnomangiz uchun domen belgilang."
        )

        await message.answer(text, parse_mode="HTML", reply_markup=site_btn())
    else:
        await message.answer(
            "⚠️ To'lovni tasdiqlashda xatolik.\n\n"
            "Iltimos, qayta urinib ko'ring yoki admin bilan bog'laning: @ndd_admin",
            reply_markup=main_kb()
        )

    await state.clear()

    # Guruhga yuborish (nazorat uchun)
    if PAYMENT_GROUP_ID:
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        caption = (
            f"💳 <b>To'lov #{code}</b> — ✅ Avto-tasdiqlandi\n\n"
            f"👤 {message.from_user.first_name} (@{message.from_user.username or '—'})\n"
            f"💰 {amount:,} so'm\n"
            f"📅 {now}"
        )
        try:
            await bot.send_photo(PAYMENT_GROUP_ID, photo=photo.file_id, caption=caption, parse_mode="HTML")
        except:
            for aid in ADMIN_IDS:
                try: await bot.send_photo(aid, photo=photo.file_id, caption=caption, parse_mode="HTML")
                except: pass

    # Log saqlash
    await save_payment_log(message.from_user.id, code, amount, photo.file_id)


@dp.message(Pay.screenshot)
async def not_photo(message: Message):
    await message.answer("📸 Iltimos, <b>rasm</b> (screenshot) yuboring!", parse_mode="HTML")


# ==================== PROFILIM ====================
@dp.message(F.text == "👤 Profilim")
async def my_profile(message: Message):
    user = await get_user_by_tg(message.from_user.id)
    if not user:
        await message.answer("Avval /start bosing!"); return

    payments = await get_user_payments(message.from_user.id)
    total = len(payments)
    confirmed = sum(1 for p in payments if p['status'] == 'paid')
    total_sum = sum(p['amount'] for p in payments if p['status'] == 'paid')

    text = (
        f"👤 <b>Profilim</b>\n\n"
        f"📛 Ism: {user['first_name'] or '—'}\n"
        f"🆔 Username: @{user['username'] or '—'}\n"
        f"🔑 Doimiy kodim: <code>{user['payment_code'] or '—'}</code>\n"
        f"<i>(bu kod har safar to'lovda ishlatiladi)</i>\n\n"
        f"📊 <b>To'lovlarim:</b>\n"
        f"  💳 Jami: {total}\n"
        f"  ✅ Tasdiqlangan: {confirmed}\n"
        f"  💰 Umumiy: {total_sum:,} so'm\n\n"
        f"📅 Ro'yxatdan: {user['created_at'].strftime('%d.%m.%Y')}\n\n"
        f"💡 <b>Profil qanday ishlaydi?</b>\n"
        f"Sizning <code>{user['payment_code'] or '—'}</code> kodingiz — bu shaxsiy raqamingiz. "
        f"Har safar premium taklifnoma olganingizda shu kod ishlatiladi. "
        f"Saytda profilingizga kirib, barcha taklifnomalaringizni boshqarishingiz mumkin."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Saytda profilim", url=f"{SITE_URL}/profile")],
        [InlineKeyboardButton(text="💳 Yangi to'lov", callback_data="new_pay")],
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data == "new_pay")
async def new_pay(cb: CallbackQuery, state: FSMContext):
    await start_payment(cb.message, state)
    await cb.answer()


# ==================== SAVOLLAR ====================
FAQ_DATA = {
    "taklifnoma": "📨 <b>Taklifnoma qanday tayyorlanadi?</b>\n\n1️⃣ Saytda shablon tanlang\n2️⃣ Ma'lumotlarni kiriting\n3️⃣ Link olasiz\n4️⃣ Do'stlaringizga yuboring\n5️⃣ Javoblarni profildan kuzating",
    "tolov": "💳 <b>Qanday to'lov qilaman?</b>\n\n1️⃣ Premium shablon tanlang → 6 raqamli kod olasiz\n2️⃣ Shu botda '💳 To'lov tasdiqlash' bosing\n3️⃣ Kodni kiriting\n4️⃣ Ko'rsatilgan kartaga to'lang\n5️⃣ Screenshot yuboring\n6️⃣ Avtomatik tasdiqlanadi — saytni yangilang!",
    "maxsus": "💎 <b>Maxsus taklifnoma nima?</b>\n\nShaxsiy talablaringiz asosida alohida domen va profillik sayt yaratib beriladi.\n\n💰 Narxi: 159,000 so'mdan\n📩 Buyurtma: @ndd_admin",
    "tasdiq": "⚡ <b>To'lov qancha vaqtda tasdiqlanadi?</b>\n\nScreenshot yuborilgandan so'ng <b>avtomatik tasdiqlanadi</b>!\n\nMuammo bo'lsa: @ndd_admin",
    "kod": "🔑 <b>Doimiy kod nima?</b>\n\nBirinchi to'lovda olingan 6 raqamli kod — sizning shaxsiy raqamingiz.\n\nHar safar premium taklifnoma olganingizda shu kod ishlatiladi.\n\nKodingizni bilish uchun 👤 Profilim tugmasini bosing.",
}

@dp.message(F.text == "❓ Savollar")
async def faq_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Taklifnoma qanday tayyorlanadi?", callback_data="faq_taklifnoma")],
        [InlineKeyboardButton(text="💳 Qanday to'lov qilaman?", callback_data="faq_tolov")],
        [InlineKeyboardButton(text="⚡ To'lov qancha vaqtda tasdiqlanadi?", callback_data="faq_tasdiq")],
        [InlineKeyboardButton(text="💎 Maxsus taklifnoma nima?", callback_data="faq_maxsus")],
        [InlineKeyboardButton(text="🔑 Doimiy kod nima?", callback_data="faq_kod")],
        [InlineKeyboardButton(text="📞 Admin bilan bog'lanish", url="https://t.me/ndd_admin")],
    ])
    await message.answer("❓ <b>Savollar</b>\n\nQuyidagilardan birini tanlang:", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data.startswith("faq_"))
async def faq_answer(cb: CallbackQuery):
    key = cb.data.replace("faq_", "")
    text = FAQ_DATA.get(key, "Ma'lumot topilmadi.")
    text += "\n\n—\n<i>Qo'shimcha: @ndd_admin yoki @Taklifnomachi_online_bot</i>"
    await cb.message.answer(text, parse_mode="HTML")
    await cb.answer()


# ==================== SAYTGA O'TISH ====================
@dp.message(F.text == "🌐 Saytga o'tish")
async def go_site(message: Message):
    await message.answer(f"🌐 <b>Taklifnomachi.online</b>", parse_mode="HTML", reply_markup=site_btn())


# ==================== ADMIN ====================
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ruxsat yo'q!"); return
    card = await get_payment_card()
    card_info = f"💳 Karta: {card['number'][-4:]}****" if card else "⚠️ Karta qo'shilmagan"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="adm_stats")],
        [InlineKeyboardButton(text="💳 To'lov kartasi", callback_data="adm_card")],
        [InlineKeyboardButton(text="📨 Ommaviy xabar", callback_data="adm_broadcast")],
    ])
    await message.answer(f"🛠 <b>Admin paneli</b>\n\n{card_info}", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "adm_stats")
async def adm_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    s = await get_stats()
    text = (
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: {s['users']}\n"
        f"💳 Jami to'lovlar: {s['payments']}\n"
        f"✅ Tasdiqlangan: {s['paid']}\n"
        f"💰 Daromad: {s['revenue']:,} so'm"
    )
    await cb.message.answer(text, parse_mode="HTML")
    await cb.answer()


@dp.callback_query(F.data == "adm_card")
async def adm_card(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    card = await get_payment_card()
    if card:
        fmt = card['number'].replace(' ','')
        fmt = ' '.join([fmt[i:i+4] for i in range(0, len(fmt), 4)])
        text = (
            f"💳 <b>Hozirgi to'lov kartasi:</b>\n\n"
            f"Raqam: <code>{fmt}</code>\n"
            f"Egasi: {card['owner']}\n"
            f"Turi: {card.get('type','HUMO')}\n\n"
            f"O'zgartirish uchun tugmani bosing:"
        )
    else:
        text = "⚠️ To'lov kartasi hali qo'shilmagan.\nYangi karta qo'shish uchun tugmani bosing:"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Yangi karta qo'shish", callback_data="adm_card_new")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")],
    ])
    await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data == "adm_card_new")
async def adm_card_new(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    await cb.message.answer("💳 Karta raqamini kiriting:\n\n<i>Masalan: 8600 1234 5678 9012</i>", parse_mode="HTML")
    await state.set_state(CardSetup.number)
    await cb.answer()

@dp.message(CardSetup.number)
async def card_number_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear(); return
    num = message.text.strip().replace(' ','').replace('-','')
    if not num.isdigit() or len(num) < 16:
        await message.answer("⚠️ Karta raqami 16 ta raqamdan iborat bo'lishi kerak. Qayta kiriting:")
        return
    await state.update_data(card_number=num)
    await message.answer("👤 Karta egasi ismini kiriting:\n\n<i>Masalan: DIYORBEK NEMATOV</i>", parse_mode="HTML")
    await state.set_state(CardSetup.owner)

@dp.message(CardSetup.owner)
async def card_owner_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear(); return
    owner = message.text.strip().upper()
    await state.update_data(card_owner=owner)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="HUMO", callback_data="ctype_HUMO"),
         InlineKeyboardButton(text="UzCard", callback_data="ctype_UZCARD")],
        [InlineKeyboardButton(text="Visa", callback_data="ctype_VISA"),
         InlineKeyboardButton(text="MasterCard", callback_data="ctype_MC")],
    ])
    await message.answer("💳 Karta turini tanlang:", reply_markup=kb)
    await state.set_state(CardSetup.card_type)

@dp.callback_query(F.data.startswith("ctype_"), CardSetup.card_type)
async def card_type_select(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    card_type = cb.data.replace("ctype_", "")
    data = await state.get_data()
    num = data.get('card_number','')
    owner = data.get('card_owner','')
    
    await set_payment_card(num, owner, card_type)
    await state.clear()
    
    fmt = ' '.join([num[i:i+4] for i in range(0, len(num), 4)])
    await cb.message.answer(
        f"✅ <b>Karta saqlandi!</b>\n\n"
        f"💳 Raqam: <code>{fmt}</code>\n"
        f"👤 Egasi: {owner}\n"
        f"📇 Turi: {card_type}\n\n"
        f"Endi foydalanuvchilar to'lov qilganda shu karta ko'rinadi.",
        parse_mode="HTML"
    )
    await cb.answer("✅ Saqlandi!")

@dp.callback_query(F.data == "adm_back")
async def adm_back(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    await cmd_admin(cb.message)
    await cb.answer()

@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    await cb.message.answer("📨 Xabarni kiriting:")
    await state.set_state(Broadcast.text)
    await cb.answer()

@dp.message(Broadcast.text)
async def adm_broadcast_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear(); return
    text = message.text or ""
    await state.clear()
    users = await get_all_user_ids()
    success = fail = 0
    for uid in users:
        try: await bot.send_message(uid, text, parse_mode="HTML"); success += 1
        except: fail += 1
        await asyncio.sleep(0.05)
    await save_broadcast(message.from_user.id, text, len(users), success, fail)
    await message.answer(f"✅ {success} ta yuborildi, ❌ {fail} ta yuborilmadi", parse_mode="HTML")


# ==================== CANCEL ====================
@dp.callback_query(F.data == "cancel")
async def cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Bekor qilindi.")
    await cb.answer()


# ==================== AUTO REPLY ====================
@dp.message()
async def auto_reply(message: Message):
    text = (message.text or "").lower()
    keywords = {
        "salom": "👋 Salom! Qanday yordam bera olaman?\n\nTugmalardan foydalaning 👇",
        "rahmat": "😊 Arzimaydi!",
        "narx": "💰 Bepul shablonlar bor!\nPremium: 15,000 so'mdan\nMaxsus: 159,000 so'mdan\n\nBatafsil: @ndd_admin",
        "tolov": FAQ_DATA["tolov"],
        "to'lov": FAQ_DATA["tolov"],
        "kod": FAQ_DATA["kod"],
        "admin": "📞 Admin: @ndd_admin",
    }
    for kw, reply in keywords.items():
        if kw in text:
            await message.answer(reply, parse_mode="HTML", reply_markup=main_kb())
            return
    await message.answer("🤔 Tushunmadim. Tugmalardan foydalaning 👇", reply_markup=main_kb())


# ==================== WEBHOOK ====================
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
