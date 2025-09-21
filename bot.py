import os
import asyncio
import asyncpg
import secrets
import time
from decimal import Decimal
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)
BOT_WALLET_ADDRESS = os.getenv("BOT_WALLET_ADDRESS", "YOUR_WALLET")
FEE_PERCENT = Decimal(os.getenv("FEE_PERCENT") or "3.0")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
pool = None  # Postgres pool

# ----------------- TRANSLATIONS -----------------
TEXTS = {
    "en": {
        "welcome": "👋 Welcome to GiftElf!\nCreate secure deals with me.",
        "new_deal": "📄 New Deal",
        "my_deals": "🔎 My Deals",
        "change_lang": "🌐 Change Language",
        "ask_amount": "Enter the amount in TON (e.g. 10.5):",
        "ask_desc": "Enter the deal description:",
        "deal_created": "✅ Deal created!",
        "menu": "Main Menu:",
        "choose_lang": "Choose your language:",
        "no_deals": "You don’t have any deals yet.",
        "deal_paid": "✅ Payment for deal {token} confirmed. Please send the NFT to the buyer.",
        "deal_received": "📦 Buyer confirmed receipt for deal {token}.",
        "deal_payout": "💸 Payout for deal {token} has been completed. Amount: {amount} TON (Fee: {fee} TON).",
        "deal_cancel": "❌ Deal {token} was cancelled.",
        "system_confirms": "The system will confirm automatically once payment is received.",
        "deal_not_found": "❌ Deal not found.",
    },
    "uk": {
        "welcome": "👋 Ласкаво просимо до GiftElf!\nСтворюй безпечні угоди зі мною.",
        "new_deal": "📄 Нова угода",
        "my_deals": "🔎 Мої угоди",
        "change_lang": "🌐 Змінити мову",
        "ask_amount": "Введіть суму в TON (наприклад 10.5):",
        "ask_desc": "Введіть опис угоди:",
        "deal_created": "✅ Угоду створено!",
        "menu": "Головне меню:",
        "choose_lang": "Оберіть мову:",
        "no_deals": "У вас ще немає угод.",
        "deal_paid": "✅ Платіж за угоду {token} підтверджено. Будь ласка, надішліть NFT покупцю.",
        "deal_received": "📦 Покупець підтвердив отримання за угодою {token}.",
        "deal_payout": "💸 Виплату за угодою {token} завершено. Сума: {amount} TON (Комісія: {fee} TON).",
        "deal_cancel": "❌ Угоду {token} скасовано.",
        "system_confirms": "Система підтвердить автоматично після отримання платежу.",
        "deal_not_found": "❌ Угоду не знайдено.",
    }
}

# ----------------- DB INIT -----------------
async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, ssl="require")
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            id SERIAL PRIMARY KEY,
            deal_token TEXT UNIQUE,
            seller_id BIGINT,
            seller_name TEXT,
            amount TEXT,
            description TEXT,
            status TEXT,
            buyer_id BIGINT,
            payment_token TEXT,
            created_at BIGINT
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id BIGINT PRIMARY KEY,
            name TEXT,
            lang TEXT DEFAULT 'en'
        )
        """)

# ----------------- HELPERS -----------------
async def get_lang(uid):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT lang FROM users WHERE tg_id=$1", uid)
    return row["lang"] if row else "en"

def main_menu(lang="en"):
    t = TEXTS[lang]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["new_deal"], callback_data="create_deal")],
        [InlineKeyboardButton(text=t["my_deals"], callback_data="my_deals")],
        [InlineKeyboardButton(text=t["change_lang"], callback_data="change_lang")]
    ])
    return kb

# ----------------- START with deep link (Buyer Link) -----------------
@dp.message(CommandStart(deep_link=True))
async def cmd_start_with_link(message: types.Message, command: CommandStart):
    uid = message.from_user.id
    lang = await get_lang(uid)
    token = command.args  # alles nach ?start=

    if token and token.startswith("join_"):
        deal_token = token.replace("join_", "")
        async with pool.acquire() as conn:
            await conn.execute("UPDATE deals SET buyer_id=$1 WHERE deal_token=$2", uid, deal_token)
            deal = await conn.fetchrow("SELECT amount,description,payment_token FROM deals WHERE deal_token=$1", deal_token)
        if deal:
            await message.answer(
                f"Deal {deal_token}\n{deal['amount']} TON\n{deal['description']}\n\n"
                f"💰 Wallet: `{BOT_WALLET_ADDRESS}`\n"
                f"Memo: `{deal['payment_token']}`\n\n"
                f"{TEXTS[lang]['system_confirms']}",
                parse_mode="Markdown"
            )
        else:
            await message.answer(TEXTS[lang]["deal_not_found"])
    else:
        await cmd_start(message)

# ----------------- START normal -----------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (tg_id,name,lang) VALUES ($1,$2,'en') "
            "ON CONFLICT (tg_id) DO UPDATE SET name=EXCLUDED.name",
            message.from_user.id, message.from_user.full_name
        )
    lang = await get_lang(message.from_user.id)
    await message.answer(TEXTS[lang]["welcome"], reply_markup=main_menu(lang))

# ----------------- CALLBACKS -----------------
user_states = {}

@dp.callback_query()
async def cb_all(cq: types.CallbackQuery):
    data = cq.data or ""
    uid = cq.from_user.id
    lang = await get_lang(uid)

    if data == "create_deal":
        user_states[uid] = {"flow":"create","step":"amount"}
        await cq.message.answer(TEXTS[lang]["ask_amount"])
        await cq.answer()
        return

    if data == "my_deals":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT deal_token,amount,description,status FROM deals WHERE seller_id=$1 OR buyer_id=$1", uid)
        if not rows:
            await cq.message.answer(TEXTS[lang]["no_deals"])
        else:
            for r in rows:
                await cq.message.answer(
                    f"Deal {r['deal_token']}\n{r['amount']} TON\n{r['description']}\nStatus: {r['status']}"
                )
        await cq.answer()
        return

    if data == "change_lang":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="English 🇬🇧", callback_data="setlang:en")],
            [InlineKeyboardButton(text="Українська 🇺🇦", callback_data="setlang:uk")]
        ])
        await cq.message.answer(TEXTS[lang]["choose_lang"], reply_markup=kb)
        await cq.answer()
        return

    if data.startswith("setlang:"):
        new_lang = data.split(":")[1]
        async with pool.acquire() as conn:
            await conn.execute("UPDATE users SET lang=$1 WHERE tg_id=$2", new_lang, uid)
        await cq.message.answer(TEXTS[new_lang]["menu"], reply_markup=main_menu(new_lang))
        await cq.answer()
        return

# ----------------- MESSAGES -----------------
@dp.message()
async def msg_handler(message: types.Message):
    uid = message.from_user.id
    txt = message.text.strip()
    lang = await get_lang(uid)

    # Admin commands
    if uid == ADMIN_ID:
        if txt.startswith("/paid "):
            token = txt.split()[1]
            async with pool.acquire() as conn:
                await conn.execute("UPDATE deals SET status='paid' WHERE deal_token=$1", token)
            await message.answer(TEXTS[lang]["deal_paid"].format(token=token))
            return
        if txt.startswith("/payout "):
            token = txt.split()[1]
            async with pool.acquire() as conn:
                deal = await conn.fetchrow("SELECT amount FROM deals WHERE deal_token=$1", token)
                if deal:
                    amt = Decimal(deal["amount"])
                    fee = (amt * FEE_PERCENT / 100).quantize(Decimal("0.0000001"))
                    payout = (amt - fee).quantize(Decimal("0.0000001"))
                    await conn.execute("UPDATE deals SET status='payout_done' WHERE deal_token=$1", token)
                    await message.answer(TEXTS[lang]["deal_payout"].format(token=token, amount=payout, fee=fee))
            return
        if txt.startswith("/cancel "):
            token = txt.split()[1]
            async with pool.acquire() as conn:
                await conn.execute("UPDATE deals SET status='cancelled' WHERE deal_token=$1", token)
            await message.answer(TEXTS[lang]["deal_cancel"].format(token=token))
            return

    # Deal creation flow
    state = user_states.get(uid)
    if state and state["flow"] == "create":
        if state["step"] == "amount":
            try:
                amt = Decimal(txt)
                if amt <= 0: raise Exception()
                state["amount"] = str(amt)
                state["step"] = "desc"
                user_states[uid] = state
                await message.answer(TEXTS[lang]["ask_desc"])
                return
            except:
                await message.answer(TEXTS[lang]["ask_amount"])
                return

        elif state["step"] == "desc":
            desc = txt
            deal_token = secrets.token_hex(6)
            payment_token = f"DEAL-{deal_token}-{secrets.token_hex(4)}"
            async with pool.acquire() as conn:
                await conn.execute("""
                INSERT INTO deals (deal_token,seller_id,seller_name,amount,description,status,payment_token,created_at)
                VALUES ($1,$2,$3,$4,$5,'open',$6,$7)
                """, deal_token, uid, message.from_user.full_name, state["amount"], desc, payment_token, int(time.time()))
            user_states.pop(uid, None)
            await message.answer(
                f"{TEXTS[lang]['deal_created']}\nToken: {deal_token}\nPayment Token: {payment_token}\n\n"
                f"Buyer Link:\nhttps://t.me/{(await bot.get_me()).username}?start=join_{deal_token}"
            )
            return

    await message.answer(TEXTS[lang]["menu"], reply_markup=main_menu(lang))

# ----------------- STARTUP -----------------
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
