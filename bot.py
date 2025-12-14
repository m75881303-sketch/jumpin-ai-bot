# bot.py
import os
import threading
import time
import requests

from flask import Flask
from dotenv import load_dotenv

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

# ----------------------------
# 1) ENV
# ----------------------------
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")

if not TELEGRAM_TOKEN:
    raise RuntimeError("Нет TELEGRAM_TOKEN (или TOKEN) в Render Environment Variables")

# HF token можно не ставить, но лучше поставить (меньше лимитов/ошибок)
# Если HF_TOKEN пустой — попробуем без авторизации (может упираться в ограничения)
HF_HEADERS = {"Accept": "image/png"}
if HF_TOKEN:
    HF_HEADERS["Authorization"] = f"Bearer {HF_TOKEN}"

HF_MODEL_URL = "https://api-inference.huggingface.co/models/stabilityai/sdxl-turbo"

# ----------------------------
# 2) WEB (для Render порта)
# ----------------------------
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

# ----------------------------
# 3) TELEGRAM BOT LOGIC
# ----------------------------
START_TEXT = "Выбери категорию:"
BTN_ART = "🎨 Арт"
ASK_PROMPT_TEXT = "Отправь текст промпта 👇\n\nНапример: «Зимний лес в стиле аниме»"

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BTN_ART, callback_data="cat_art")]]
    )

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(START_TEXT, reply_markup=main_menu_keyboard())

async def on_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "cat_art":
        context.user_data["awaiting_prompt"] = True
        await q.message.reply_text(ASK_PROMPT_TEXT)
    else:
        await q.message.reply_text("Неизвестная категория. Нажми /start")

def hf_generate_image_bytes(prompt: str) -> bytes:
    payload = {
        "inputs": prompt,
        "parameters": {
            "num_inference_steps": 1,
            "guidance_scale": 0.0
        }
    }

    r = requests.post(
        HF_MODEL_URL,
        headers=HF_HEADERS,
        json=payload,
        timeout=90,
    )

    # У HF иногда бывает очередь/прогрев модели:
    if r.status_code == 503:
        try:
            data = r.json()
            wait_s = int(data.get("estimated_time", 10))
        except Exception:
            wait_s = 10
        time.sleep(min(max(wait_s, 5), 25))
        r = requests.post(HF_MODEL_URL, headers=HF_HEADERS, json=payload, timeout=90)

    if r.status_code != 200:
        # Пытаемся красиво достать текст ошибки
        err_text = ""
        try:
            err_text = r.json()
        except Exception:
            err_text = r.text[:500]
        raise RuntimeError(f"HF error {r.status_code}: {err_text}")

    return r.content

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_prompt"):
        await update.message.reply_text("Нажми /start и выбери категорию 🙂")
        return

    prompt = (update.message.text or "").strip()
    if not prompt:
        await update.message.reply_text("Пришли текст промпта 🙂")
        return

    context.user_data["awaiting_prompt"] = False

    msg = await update.message.reply_text("Генерирую картинку... ⏳")

    try:
        img_bytes = hf_generate_image_bytes(prompt)
        await update.message.reply_photo(photo=img_bytes, caption=f"✅ Готово!\n\nПромпт: {prompt}")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(
            "Ошибка генерации 😕\n\n"
            f"{e}\n\n"
            "Нажми /start и попробуй ещё раз."
        )

def run_bot():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(on_category))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # drop_pending_updates=True — помогает убрать старые апдейты и снижает шанс конфликтов
    app.run_polling(drop_pending_updates=True)

# ----------------------------
# 4) ENTRYPOINT
# ----------------------------
if __name__ == "__main__":
    # Важно: сначала поднимаем веб-сервер для Render, потом бот
    threading.Thread(target=run_web, daemon=True).start()
    run_bot()
