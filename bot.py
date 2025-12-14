import os
import threading

from flask import Flask

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

# --- Flask (чтобы Render видел порт) ---
web_app = Flask(__name__)

@web_app.get("/")
def home():
    return "ok", 200

@web_app.get("/healthz")
def healthz():
    return "ok", 200

def run_web():
    port = int(os.getenv("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port)

# --- Telegram bot ---
TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TOKEN") or ""

START_TEXT = "Привет! Нажми кнопку и отправь промпт."
BTN_TEXT = "Отправить промпт"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(BTN_TEXT, callback_data="send_prompt")]]
    await update.message.reply_text(START_TEXT, reply_markup=InlineKeyboardMarkup(keyboard))

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "send_prompt":
        context.user_data["await_prompt"] = True
        await q.edit_message_text("Ок ✅\n\nОтправь мне текст промпта следующим сообщением.")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_prompt"):
        return

    prompt = update.message.text
    context.user_data["await_prompt"] = False

    # тут потом добавим твою логику (Kling/и т.д.), пока просто подтверждение
    await update.message.reply_text(f"Принято ✅\n\nПромпт:\n{prompt}")

def main():
    if not TOKEN:
        raise RuntimeError("Нет TELEGRAM_TOKEN (или TOKEN) в переменных окружения Render")

    # 1) Сначала поднимаем Flask в отдельном потоке (Render увидит порт)
    threading.Thread(target=run_web, daemon=True).start()

    # 2) Потом запускаем телеграм-бота
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling()

if __name__ == "__main__":
    main()
