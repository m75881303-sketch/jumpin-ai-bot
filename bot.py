import os
import base64
import threading
from io import BytesIO

from flask import Flask

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

from openai import OpenAI


# ========== ENV ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("Нет TELEGRAM_TOKEN в Render Environment Variables")
if not OPENAI_API_KEY:
    raise RuntimeError("Нет OPENAI_API_KEY в Render Environment Variables")

client = OpenAI(api_key=OPENAI_API_KEY)

# ========== FLASK (для Render healthcheck / порт-скан) ==========
web_app = Flask(__name__)

@web_app.get("/")
def root():
    return "ok", 200

@web_app.get("/healthz")
def healthz():
    return "ok", 200

def run_web():
    port = int(os.getenv("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port)


# ========== TELEGRAM BOT ==========
START_TEXT = "Выбери категорию:"
BTN_ART = "🎨 Арт"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(BTN_ART, callback_data="art")]]
    await update.message.reply_text(
        START_TEXT,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "art":
        context.user_data["await_prompt"] = True
        await q.edit_message_text("Отправь текст промпта 👇\n\nНапример: «Зимний лес в стиле аниме»")

async def generate_image_bytes(prompt: str) -> BytesIO:
    """
    Генерирует картинку через OpenAI Images и возвращает BytesIO,
    чтобы отправить в Telegram как фото.
    """
    resp = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1024",
        # чтобы не возиться с URL — берём base64
        response_format="b64_json",
    )

    b64 = resp.data[0].b64_json
    img_bytes = base64.b64decode(b64)
    bio = BytesIO(img_bytes)
    bio.name = "image.png"
    bio.seek(0)
    return bio

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_prompt"):
        await update.message.reply_text("Нажми /start и выбери «🎨 Арт».")
        return

    context.user_data["await_prompt"] = False
    prompt = (update.message.text or "").strip()

    if not prompt:
        await update.message.reply_text("Промпт пустой. Нажми /start и попробуй снова.")
        return

    msg = await update.message.reply_text("Принято ✅\nГенерирую картинку… ⏳")

    try:
        img = await generate_image_bytes(prompt)
        await update.message.reply_photo(photo=img, caption=f"Готово ✅\n\nПромпт: {prompt}")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"Ошибка генерации 😕\n\n{e}\n\nНажми /start и попробуй ещё раз.")


def main():
    # ВАЖНО: Render Web Service ждёт открытый порт → поднимаем Flask в отдельном потоке
    threading.Thread(target=run_web, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling()


if __name__ == "__main__":
    main()
