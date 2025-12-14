import os
import threading
from flask import Flask
from openai import OpenAI

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# ENV
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("Нет TELEGRAM_TOKEN (или TOKEN) в Render Environment Variables")
if not OPENAI_API_KEY:
    raise RuntimeError("Нет OPENAI_API_KEY (или OPENAI_KEY) в Render Environment Variables")

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# WEB (Render needs a port)
# =========================
web = Flask(__name__)

@web.get("/")
def home():
    return "ok", 200

@web.get("/healthz")
def healthz():
    return "ok", 200

def run_web():
    port = int(os.getenv("PORT", "10000"))
    web.run(host="0.0.0.0", port=port)

# =========================
# BOT LOGIC
# =========================
START_TEXT = "Выбери категорию:"
BTN_ART = "🎨 Арт"

ASK_PROMPT_TEXT = "Отправь текст промпта 👇\n\nНапример: «Зимний лес в стиле аниме»"

def build_keyboard():
    keyboard = [[InlineKeyboardButton(BTN_ART, callback_data="art")]]
    return InlineKeyboardMarkup(keyboard)

def generate_image_url(prompt: str) -> str:
    # ВАЖНО: никаких response_format тут НЕ нужно
    result = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1024",
    )
    return result.data[0].url

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(START_TEXT, reply_markup=build_keyboard())

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "art":
        context.user_data["mode"] = "art"
        context.user_data["await_prompt"] = True
        await q.edit_message_text(ASK_PROMPT_TEXT)

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_prompt"):
        await update.message.reply_text("Нажми /start 🙂")
        return

    context.user_data["await_prompt"] = False
    prompt = (update.message.text or "").strip()

    if not prompt:
        context.user_data["await_prompt"] = True
        await update.message.reply_text("Промпт пустой. Отправь текст ещё раз 👇")
        return

    # Сообщим, что начали
    await update.message.reply_text("Генерирую картинку… ⏳")

    try:
        img_url = generate_image_url(prompt)

        await update.message.reply_photo(
            photo=img_url,
            caption=f"✅ Готово!\n\nПромпт:\n{prompt}\n\nНажми /start чтобы сделать ещё.",
        )

    except Exception as e:
        # Покажем ошибку (но без падения бота)
        await update.message.reply_text(
            "Ошибка генерации 😕\n"
            f"{e}\n\nНажми /start и попробуй ещё раз."
        )

def main():
    # Запускаем веб-сервер для Render (порт/healthcheck)
    threading.Thread(target=run_web, daemon=True).start()

    # Запускаем Telegram polling
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
