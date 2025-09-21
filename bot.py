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

bot = Bot(token=BOT_TOKEN, parse_mode="MarkdownV2")
dp = Dispatcher()
pool = None

# ----------------- ESCAPE FUNCTION -----------------
def escape_md(text: str) -> str:
    escape_chars = r"\_*[]()~`>#+-=|{}.!"
    return "".join("\\"+c if c in escape_chars else c for c in text)

# ----------------- TRANSLATIONS -----------------
TEXTS = {
    "en": {
        "welcome": "👋 *Welcome to GiftElf!*\n\nYour friendly escrow bot for safe & simple 🎁 gift deals.\n\nStart by creating a deal below 👇",
        "new_deal": "📄 Create New Deal",
        "my_deals": "🔎 My Deals",
        "change_lang": "🌐 Change Language",
        "ask_amount": "💰 *Enter the deal amount in TON* (e.g. `10.5`):",
        "ask_desc": "📝 *Enter a short description for this deal:*",
        "deal_created": "✅ *Your deal is ready!*",
        "menu": "📍 *Main Menu*",
        "choose_lang": "🌐 Please choose your language:",
        "no_deals": "ℹ️ You don’t have any deals yet.",
        "deal_paid_admin": "✅ Deal *{token}* marked as *paid*. Buyer & Seller have been notified 🎉",
        "buyer_paid_msg": "🎉 *We got your payment!*\n\nForward this message to the seller so they can send you the 🎁 gift.\n\nOnce you receive it, confirm below 👇",
        "btn_received": "📥 I got my gift",
        "btn_sent": "📤 I sent the gift",
        "buyer_received_thanks": "🎁 *Awesome!* You confirmed receipt. Deal completed successfully 🎊",
        "admin_buyer_received": "📦 Buyer confirmed receipt for deal *{token}* ✅",
        "seller_paid_msg": "💰 *Good news!*\n\nThe payment for deal *{token}* is confirmed ✅\n\n👉 Please send the 🎁 gift to the buyer and then confirm below:",
        "seller_sent_ok": "📤 You confirmed you sent the gift. Great job 👍",
        "buyer_seller_sent": "📦 *Your gift is on the way!*\n\nThe seller confirmed shipping.\n\n👉 Confirm below once it’s in your hands 👇",
        "deal_payout": "💸 *Payout completed!*\n\nDeal: *{token}*\nYou received *{amount} TON* (Fee: {fee} TON).",
        "deal_cancel": "❌ Deal *{token}* has been cancelled by admin.",
        "deal_not_found": "❌ Sorry, this deal was not found.",
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

# ----------------- START with deep link (Buyer) -----------------
@dp.message(CommandStart(deep_link=True))
async def cmd_start_with_link(message: types.Message, command: CommandStart):
    uid = message.from_user.id
    lang = await get_lang(uid)
    token = command.args

    if token and token.startswith("join_"):
        deal_token = token.replace("join_", "")
        async with pool.acquire() as conn:
            await conn.execute("UPDATE deals SET buyer_id=$1 WHERE deal_token=$2", uid, deal_token)
            deal = await conn.fetchrow("SELECT amount,description,payment_token FROM deals WHERE deal_token=$1", deal_token)
        if deal:
            await message.answer(
                f"🎁 *Deal {escape_md(deal_token)}*\n\n"
                f"💰 Amount: *{escape_md(deal['amount'])} TON*\n"
                f"📝 {escape_md(deal['description'])}\n\n"
                f"💳 Wallet: `{escape_md(BOT_WALLET_ADDRESS)}`\n"
                f"🔑 Memo: `{escape_md(deal['payment_token'])}`",
                parse_mode="MarkdownV2"
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

    if data.startswith("confirm_sent:"):
        token = data.split(":")[1]
        async with pool.acquire() as conn:
            deal = await conn.fetchrow("SELECT buyer_id FROM deals WHERE deal_token=$1", token)
            await conn.execute("UPDATE deals SET status='sent' WHERE deal_token=$1", token)
        await cq.message.answer(TEXTS[lang]["seller_sent_ok"])
        if deal and deal["buyer_id"]:
            buyer_lang = await get_lang(deal["buyer_id"])
            await bot.send_message(
                chat_id=deal["buyer_id"],
                text=TEXTS[buyer_lang]["buyer_seller_sent"]
            )
        await cq.answer()
        return

    if data.startswith("confirm_received:"):
        token = data.split(":")[1]
        async with pool.acquire() as conn:
            await conn.execute("UPDATE deals SET status='received' WHERE deal_token=$1", token)
        await cq.message.answer(TEXTS[lang]["buyer_received_thanks"])
        if ADMIN_ID:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=TEXTS["en"]["admin_buyer_received"].format(token=escape_md(token)),
                parse_mode="MarkdownV2"
            )
        await cq.answer()
        return

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
                    f"🔑 Deal: *{escape_md(r['deal_token'])}*\n"
                    f"💰 {escape_md(r['amount'])} TON\n"
                    f"📝 {escape_md(r['description'])}\n"
                    f"📌 Status: *{escape_md(r['status'])}*",
                    parse_mode="MarkdownV2"
                )
        await cq.answer()
        return

# ----------------- MESSAGES -----------------
@dp.message()
async def msg_handler(message: types.Message):
    uid = message.from_user.id
    txt = message.text.strip()
    lang = await get_lang(uid)

    # Deal creation
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
            deal_token = secrets.token_hex(6)  # kurzer Token
            payment_token = f"NOKUZ-{deal_token}-{secrets.token_hex(4)}"  # immer NOKUZ!

            async with pool.acquire() as conn:
                await conn.execute("""
                INSERT INTO deals (deal_token,seller_id,seller_name,amount,description,status,payment_token,created_at)
                VALUES ($1,$2,$3,$4,$5,'open',$6,$7)
                """, deal_token, uid, message.from_user.full_name, state["amount"], desc, payment_token, int(time.time()))

            user_states.pop(uid, None)
            bot_username = (await bot.get_me()).username

            await message.answer(
                f"{TEXTS[lang]['deal_created']}\n\n"
                f"🔑 Deal Token: `{escape_md(deal_token)}`\n"
                f"🪙 Payment Token: `{escape_md(payment_token)}`\n\n"
                f"👥 Share this link with the buyer:\n"
                f"https://t.me/{escape_md(bot_username)}?start=join_{escape_md(deal_token)}",
                parse_mode="MarkdownV2"
            )
            return

# ----------------- STARTUP -----------------
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
