# ===============================
# Render + Telegram bot (WORKING)
# ===============================

import os
import threading
from flask import Flask

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------- ENV ----------
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("Нет TELEGRAM_TOKEN в Render Environment Variables")

# ---------- FLASK (для Render health check) ----------
app = Flask(__name__)

@app.get("/")
def home():
    return "ok", 200

@app.get("/healthz")
def health():
    return "ok", 200


def run_web():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


# ---------- TELEGRAM BOT ----------
START_TEXT = "Привет! Нажми кнопку ниже 👇"
BTN_TEXT = "Отправить промпт"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(BTN_TEXT, callback_data="send_prompt")]]
    await update.message.reply_text(
        START_TEXT,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "send_prompt":
        context.user_data["await_prompt"] = True
        await query.edit_message_text("Отправь текст 👇")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_prompt"):
        return

    text = update.message.text
    context.user_data["await_prompt"] = False

    await update.message.reply_text(
        f"Принято ✅\n\nТвой текст:\n{text}"
    )


def run_bot():
    app_bot = ApplicationBuilder().token(TOKEN).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CallbackQueryHandler(on_button))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app_bot.run_polling()


# ---------- MAIN ----------
if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run_bot()
