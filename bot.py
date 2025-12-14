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

# ---------- Flask (чтобы Render видел открытый порт) ----------
app_web = Flask(__name__)

@app_web.get("/")
def home():
    return "ok", 200


def run_web():
    port = int(os.getenv("PORT", "10000"))
    app_web.run(host="0.0.0.0", port=port)


# ---------- Telegram bot логика ----------
MENU_BTN_TEXT = "Отправить промпт"
CALLBACK_SEND_PROMPT = "send_prompt"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(MENU_BTN_TEXT, callback_data=CALLBACK_SEND_PROMPT)]]
    )
    await update.message.reply_text("Нажми кнопку 👇", reply_markup=keyboard)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == CALLBACK_SEND_PROMPT:
        context.user_data["await_prompt"] = True
        await q.edit_message_text("Отправь текст промпта одним сообщением.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ждём промпт только если пользователь нажал кнопку
    if not context.user_data.get("await_prompt"):
        await update.message.reply_text("Нажми /start и кнопку, чтобы отправить промпт.")
        return

    prompt = update.message.text
    context.user_data["await_prompt"] = False

    # Тут ты потом можешь вызвать OpenAI, если надо.
    # Сейчас просто подтверждаем получение:
    await update.message.reply_text(f"Принято ✅\n\nПромпт:\n{prompt}")


def main():
    load_dotenv()

    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Не найден TELEGRAM_TOKEN в переменных окружения Render")

    # Flask запускаем в отдельном потоке, чтобы Render видел порт
    threading.Thread(target=run_web, daemon=True).start()

    # Telegram polling
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling()


if __name__ == "__main__":
    main()
