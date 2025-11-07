# main_ai_bot.py — Telegram бот: калории + Stars
import os, json
from datetime import datetime, timedelta, timezone
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, LabeledPrice
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters

DB_PATH = os.getenv("CAL_DB_PATH", "calories_bot.db")
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://calories-webapp.onrender.com")

MONTH_PRICE_STARS = 599
CURRENCY = "XTR"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    now = datetime.now(timezone.utc)
    trial_until = (now + timedelta(days=7)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (telegram_id, daily_goal, plan, trial_until) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET trial_until = COALESCE(users.trial_until, excluded.trial_until)",
            (tg_id, 2000, "trial", trial_until)
        )
        await db.commit()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть трекер", web_app=WebAppInfo(url=WEBAPP_URL))],
                               [InlineKeyboardButton("Оформить PRO 599⭐", callback_data="subscribe")]])
    await update.message.reply_text("Добро пожаловать! 7-дневный триал активирован.", reply_markup=kb)

async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = "Calories PRO — 1 месяц"
    description = "ИИ-распознавание фото, отчёты и мониторинг."
    payload = f"sub_monthly:{update.effective_user.id}:{int(datetime.now(timezone.utc).timestamp())}"
    prices = [LabeledPrice(label="Monthly PRO", amount=MONTH_PRICE_STARS)]
    await update.message.reply_invoice(
        title=title,
        description=description,
        payload=payload,
        provider_token="",  # Stars
        currency=CURRENCY,
        prices=prices
    )

async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    tg_id = update.effective_user.id
    now = datetime.now(timezone.utc)
    renews_at = now + timedelta(days=30)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (tg_id,))
        await db.execute("UPDATE users SET plan = ?, renews_at = ?, payments_provider = ? WHERE telegram_id = ?",
                         ("pro", renews_at.isoformat(), "stars", tg_id))
        await db.execute("INSERT INTO payments (telegram_id, created_at, provider, amount_cents, currency, period_months, status, provider_payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (tg_id, now.isoformat(), "stars", MONTH_PRICE_STARS, CURRENCY, 1, "paid", json.dumps(sp.to_dict())))
        await db.commit()
    await update.message.reply_text("Спасибо! Подписка PRO активирована на 1 месяц ✅")

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("subscribe", subscribe_cmd))
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
