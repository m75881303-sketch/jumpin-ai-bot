import os
import io
import time
import threading
import logging
from typing import Dict, Tuple

import requests
from flask import Flask

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -----------------------------
# LOGGING
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("jump-bot")

# -----------------------------
# ENV
# -----------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()  # Hugging Face access token (read)

if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ Нет TELEGRAM_TOKEN в переменных окружения Render")

# -----------------------------
# FLASK (healthcheck for Render)
# -----------------------------
app = Flask(__name__)

@app.get("/")
def root():
    return "ok", 200

@app.get("/healthz")
def healthz():
    return "ok", 200

def run_flask():
    port = int(os.getenv("PORT", "10000"))
    # важно: use_reloader=False, иначе будет 2 процесса -> конфликт бота
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# -----------------------------
# UI / MENU STRUCTURE
# start -> language -> main -> design -> hf -> size -> prompt loop
# -----------------------------
LANGS = [
    ("ru", "Русский"),
    ("en", "English"),
]

# Пиши сюда модели, которые реально поддерживаются через HF Inference Router.
# Если будет 404 — значит модель/провайдер недоступны через hf-inference router.
HF_MODELS: Dict[str, str] = {
    "SDXL (stabilityai)": "stabilityai/stable-diffusion-xl-base-1.0",
    "SD v1.5 (runwayml)": "runwayml/stable-diffusion-v1-5",
    # Если хочешь FLUX — часто он НЕ доступен через hf-inference router => будет 404.
    # "FLUX schnell": "black-forest-labs/FLUX.1-schnell",
}

ASPECTS: Dict[str, Tuple[int, int]] = {
    "1:1": (1024, 1024),
    "9:16": (768, 1365),
    "16:9": (1365, 768),
}

K_LANG = "lang"
K_MODEL = "hf_model"
K_ASPECT = "aspect"
K_EXPECT_PROMPT = "expect_prompt"

def kb_language():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(title, callback_data=f"lang:{code}")]
         for code, title in LANGS]
    )

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Дизайн с ИИ", callback_data="main:design")],
    ])

def kb_design():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤗 Hugging Face", callback_data="design:hf")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back:main")],
    ])

def kb_hf_models():
    rows = []
    for title, model_id in HF_MODELS.items():
        rows.append([InlineKeyboardButton(title, callback_data=f"hfmodel:{model_id}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back:design")])
    return InlineKeyboardMarkup(rows)

def kb_sizes():
    rows = [[InlineKeyboardButton(a, callback_data=f"size:{a}")] for a in ASPECTS.keys()]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back:hf")])
    return InlineKeyboardMarkup(rows)

def kb_after_prompt():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📐 Размер", callback_data="menu:size"),
         InlineKeyboardButton("⬅️ В меню", callback_data="menu:main")],
    ])

# -----------------------------
# HuggingFace call (router)
# -----------------------------
def hf_generate_image(model_id: str, prompt: str, width: int, height: int) -> bytes:
    """
    Hugging Face Inference Router.
    Важно: токен должен иметь разрешение на Inference Providers.
    """
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN не задан. Добавь его в Render → Environment.")

    model_id = (model_id or "").strip()
    if not model_id:
        raise RuntimeError("Пустой model_id")

    url = f"https://router.huggingface.co/hf-inference/models/{model_id}"
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Accept": "image/png",
    }

    payload = {
        "inputs": prompt,
        "parameters": {"width": int(width), "height": int(height)},
    }

    r = requests.post(url, headers=headers, json=payload, timeout=180)

    if r.status_code == 200:
        return r.content

    # показать понятную ошибку
    try:
        err = r.json()
    except Exception:
        err = {"error": r.text[:500]}

    raise RuntimeError(f"HF error {r.status_code}: {err}")

# -----------------------------
# Telegram Handlers
# -----------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[K_EXPECT_PROMPT] = False
    await update.message.reply_text("Выбери язык 👇", reply_markup=kb_language())

async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[K_EXPECT_PROMPT] = False
    await update.message.reply_text("Главное меню 👇", reply_markup=kb_main())

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data.startswith("lang:"):
        context.user_data[K_LANG] = data.split(":", 1)[1]
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("Главное меню 👇", reply_markup=kb_main())
        return

    if data == "main:design":
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("🎨 Дизайн с ИИ — выбери источник:", reply_markup=kb_design())
        return

    if data == "design:hf":
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("🤗 Hugging Face — выбери модель:", reply_markup=kb_hf_models())
        return

    if data.startswith("hfmodel:"):
        model_id = data.split(":", 1)[1].strip()
        context.user_data[K_MODEL] = model_id
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("Выбери размер (соотношение сторон):", reply_markup=kb_sizes())
        return

    if data.startswith("size:"):
        aspect = data.split(":", 1)[1].strip()
        context.user_data[K_ASPECT] = aspect
        context.user_data[K_EXPECT_PROMPT] = True

        model_id = context.user_data.get(K_MODEL, "")
        await q.edit_message_text(
            "✍️ Отправь текст промпта.\n\n"
            f"Модель: {model_id}\n"
            f"Размер: {aspect}\n\n"
            "После генерации просто пиши следующий промпт — /start больше не нужен.",
            reply_markup=kb_after_prompt(),
        )
        return

    if data == "menu:size":
        await q.edit_message_text("Выбери размер:", reply_markup=kb_sizes())
        return

    if data == "menu:main":
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("Главное меню 👇", reply_markup=kb_main())
        return

    if data == "back:main":
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("Главное меню 👇", reply_markup=kb_main())
        return

    if data == "back:design":
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("🎨 Дизайн с ИИ — выбери источник:", reply_markup=kb_design())
        return

    if data == "back:hf":
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("🤗 Hugging Face — выбери модель:", reply_markup=kb_hf_models())
        return

    await q.edit_message_text("Не поняла действие. Нажми /menu", reply_markup=kb_main())

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    if not context.user_data.get(K_EXPECT_PROMPT, False):
        await update.message.reply_text("Открой меню 👇", reply_markup=kb_main())
        return

    model_id = (context.user_data.get(K_MODEL) or "").strip()
    aspect = (context.user_data.get(K_ASPECT) or "1:1").strip()

    if not model_id:
        context.user_data[K_EXPECT_PROMPT] = False
        await update.message.reply_text("Сначала выбери модель в меню 👇", reply_markup=kb_main())
        return

    w, h = ASPECTS.get(aspect, (1024, 1024))

    try:
        await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)

        # блокирующий requests -> в отдельный поток
        img_bytes = await asyncio_to_thread(hf_generate_image, model_id, text, w, h)

        bio = io.BytesIO(img_bytes)
        bio.name = "image.png"
        bio.seek(0)

        await update.message.reply_photo(photo=bio, caption="✅ Готово!", reply_markup=kb_after_prompt())
        context.user_data[K_EXPECT_PROMPT] = True

    except Exception as e:
        log.exception("Generation failed")
        await update.message.reply_text(
            "😕 Ошибка генерации:\n"
            f"{e}\n\n"
            "Можешь отправить другой промпт или поменять размер/модель.",
            reply_markup=kb_after_prompt(),
        )
        context.user_data[K_EXPECT_PROMPT] = True

# ---- маленький async-to-thread без проблем на 3.13
async def asyncio_to_thread(func, *args, **kwargs):
    result = []
    exc = []

    def runner():
        try:
            result.append(func(*args, **kwargs))
        except Exception as e:
            exc.append(e)

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    while t.is_alive():
        time.sleep(0.05)

    if exc:
        raise exc[0]
    return result[0]

# -----------------------------
# Telegram Runner (polling)
# -----------------------------
def run_telegram_polling():
    if not TELEGRAM_TOKEN:
        log.error("❌ TELEGRAM_TOKEN missing")
        return

    log.info("✅ Starting Telegram polling...")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Важно: если где-то был webhook — polling не будет работать.
    # PTB сам дергает deleteWebhook внутри run_polling, но оставим как есть.
    application.run_polling(drop_pending_updates=# -----------------------------
# MAIN
# -----------------------------
if __name__ == "__main__":
    # 1) Flask в отдельном потоке (healthcheck для Render)
    threading.Thread(target=run_flask, daemon=True).start()

    # 2) Telegram polling — В ГЛАВНОМ ПОТОКЕ
    log.info("✅ Starting Telegram polling in main thread...")
    run_telegram_polling()
