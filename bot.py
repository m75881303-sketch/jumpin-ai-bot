import os
import asyncio
import threading
from io import BytesIO

import requests
from flask import Flask as FlaskApp
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

# -----------------------------
# Render health check (Flask)
# -----------------------------
web = FlaskApp(__name__)

@web.get("/")
def root():
    return "ok", 200

@web.get("/healthz")
def healthz():
    return "ok", 200

def run_web():
    port = int(os.getenv("PORT", "10000"))
    web.run(host="0.0.0.0", port=port)

# -----------------------------
# Config from ENV (Render)
# -----------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")

# Модель HF (можешь менять потом)
HF_MODEL = os.getenv("HF_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")

START_TEXT = "Выбери категорию:"
ASK_PROMPT_TEXT = 'Отправь текст промпта 👇\n\nНапример: «Зимний лес в стиле аниме»'

# -----------------------------
# Hugging Face Router генерация
# -----------------------------
def hf_generate_image_bytes(prompt: str) -> bytes:
    """
    Возвращает байты PNG/JPG картинки от Hugging Face Router.
    """
    if not HF_TOKEN:
        raise RuntimeError("Нет HF_TOKEN в Render Environment Variables")

    url = f"https://router.huggingface.co/hf-inference/models/{HF_MODEL}"
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "image/png",
    }
    payload = {"inputs": prompt}

    r = requests.post(url, headers=headers, json=payload, timeout=180)

    # Если вернулся JSON (обычно это ошибка)
    content_type = r.headers.get("content-type", "")
    if "application/json" in content_type:
        raise RuntimeError(f"HF error {r.status_code}: {r.text}")

    r.raise_for_status()
    return r.content

async def generate_image_async(prompt: str) -> bytes:
    # requests блокирует поток — уводим в отдельный поток
    return await asyncio.to_thread(hf_generate_image_bytes, prompt)

# -----------------------------
# Telegram handlers
# -----------------------------
def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Арт", callback_data="art")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(START_TEXT, reply_markup=main_keyboard())

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "art":
        context.user_data["mode"] = "art"
        context.user_data["await_prompt"] = True
        await q.edit_message_text(ASK_PROMPT_TEXT)

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ждём текст промпта только после выбора категории
    if not context.user_data.get("await_prompt"):
        await update.message.reply_text("Нажми /start 🙂")
        return

    prompt = update.message.text.strip()
    context.user_data["await_prompt"] = False  # сбрасываем ожидание

    # Быстрое подтверждение
    await update.message.reply_text("Генерирую картинку… ⏳")

    try:
        img_bytes = await generate_image_async(prompt)

        bio = BytesIO(img_bytes)
        bio.name = "image.png"
        bio.seek(0)

        await update.message.reply_photo(photo=bio, caption="✅ Готово!")
    except Exception as e:
        # Возвращаем пользователя в режим ожидания промпта (чтобы мог отправить ещё раз)
        context.user_data["await_prompt"] = True
        await update.message.reply_text(
            f"Ошибка генерации 😕\n\n{e}\n\nНажми /start и попробуй ещё раз."
        )

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Нажми /start 🙂")

def build_app():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Нет TELEGRAM_TOKEN (или TOKEN) в Render Environment Variables")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    return app

def run_bot():
    app = build_app()
    # drop_pending_updates помогает после перезапусков не ловить старые апдейты
    app.run_polling(drop_pending_updates=True)

# -----------------------------
# Entry point
# -----------------------------
if __name__ == "__main__":
    # Flask для Render healthcheck — в отдельном потоке
    t = threading.Thread(target=run_web, daemon=True)
    t.start()

    # Telegram bot polling
    run_bot()
