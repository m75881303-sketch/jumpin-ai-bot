import os
import threading
from io import BytesIO

import httpx
from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# Config (Render Env Vars)
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")

# Можно менять модель через Render ENV: HF_MODEL
# Более лёгкая и обычно стабильная для free-tier:
HF_MODEL = os.getenv("HF_MODEL", "runwayml/stable-diffusion-v1-5")

if not TELEGRAM_TOKEN:
    raise RuntimeError("Нет TELEGRAM_TOKEN в переменных окружения Render")
if not HF_TOKEN:
    raise RuntimeError("Нет HF_TOKEN в переменных окружения Render")

# =========================
# Web app for Render health check
# =========================
web_app = Flask(__name__)

@web_app.get("/")
def home():
    return "ok", 200

@web_app.get("/healthz")
def healthz():
    return "ok", 200

def run_web():
    port = int(os.getenv("PORT", "10000"))
    # ВАЖНО: use_reloader=False чтобы не запускалось два процесса (иначе конфликт Telegram getUpdates)
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# =========================
# Hugging Face image generator
# =========================
async def generate_image_hf(prompt: str) -> BytesIO:
    """
    Returns BytesIO with image data or raises RuntimeError with HF error message.
    """
    url = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}

    payload = {
        "inputs": prompt,
        "parameters": {
            "num_inference_steps": 25,
            "guidance_scale": 7.0
        }
    }

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, headers=headers, json=payload)

    # HF может вернуть JSON с ошибкой
    ct = r.headers.get("content-type", "")
    if "application/json" in ct:
        data = r.json()
        # Примеры: {"error":"Model ... is currently loading"} или {"error":"..."}
        msg = data.get("error") or str(data)
        raise RuntimeError(msg)

    if r.status_code != 200:
        raise RuntimeError(f"HF error {r.status_code}: {r.text[:300]}")

    # Обычно возвращаются "сырые" байты изображения
    bio = BytesIO(r.content)
    bio.name = "image.png"
    bio.seek(0)
    return bio

# =========================
# Telegram bot logic
# =========================
START_TEXT = "Выбери категорию:"
BTN_ART = "🎨 Арт"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(BTN_ART, callback_data="cat_art")]
    ]
    await update.message.reply_text(
        START_TEXT,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "cat_art":
        context.user_data["mode"] = "art"
        context.user_data["await_prompt"] = True
        await q.edit_message_text(
            'Отправь текст промпта 👇\n\nНапример: «Зимний лес в стиле аниме»'
        )
        return

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_prompt"):
        return

    prompt = (update.message.text or "").strip()
    if not prompt:
        await update.message.reply_text("Пришли текст промпта 🙌")
        return

    context.user_data["await_prompt"] = False

    mode = context.user_data.get("mode")
    if mode != "art":
        await update.message.reply_text("Не понял режим. Нажми /start ещё раз.")
        return

    try:
        await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)
        img = await generate_image_hf(prompt)
        await update.message.reply_photo(
            photo=img,
            caption=f"Готово ✅\n\nПромпт:\n{prompt}"
        )
    except Exception as e:
        # Частые HF ошибки: модель грузится / очередь / лимиты free tier
        await update.message.reply_text(
            "Ошибка генерации 😕\n\n"
            f"{str(e)}\n\n"
            "Нажми /start и попробуй ещё раз."
        )

def run_bot():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    # Flask для Render (порт и healthcheck)
    threading.Thread(target=run_web, daemon=True).start()
    # Telegram polling (ОДИН экземпляр)
    run_bot()
