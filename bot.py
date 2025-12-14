import os
import io
import asyncio
import logging
from typing import Dict, Tuple, Optional

import requests
from flask import Flask

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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
logger = logging.getLogger("jump-bot")

# -----------------------------
# ENV
# -----------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()

if not TELEGRAM_TOKEN:
    raise RuntimeError("Нет TELEGRAM_TOKEN в переменных окружения Render")

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
    # Render обычно прокидывает PORT
    port = int(os.getenv("PORT", "10000"))
    # Важно: без reloader, иначе будет 2 процесса
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# -----------------------------
# UI / MENU STRUCTURE
# start -> language -> main -> design -> hf models -> size -> prompt loop
# -----------------------------
LANGS = [
    ("ru", "Русский"),
    ("en", "English"),
]

# Модели (можешь менять/добавлять кнопками — HF_MODEL не нужен)
# ВАЖНО: это ID модели на HuggingFace
HF_MODELS: Dict[str, str] = {
    "FLUX schnell (быстро)": "black-forest-labs/FLUX.1-schnell",
    "SDXL": "stabilityai/stable-diffusion-xl-base-1.0",
}

ASPECTS: Dict[str, Tuple[int, int]] = {
    "1:1": (1024, 1024),
    "9:16": (768, 1365),
    "16:9": (1365, 768),
}

# keys in context.user_data
K_LANG = "lang"
K_MODEL = "hf_model"
K_ASPECT = "aspect"
K_EXPECT_PROMPT = "expect_prompt"

# -----------------------------
# Helpers for keyboards
# -----------------------------
def kb_language():
    rows = []
    for code, title in LANGS:
        rows.append([InlineKeyboardButton(title, callback_data=f"lang:{code}")])
    return InlineKeyboardMarkup(rows)

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
    rows = []
    for a in ASPECTS.keys():
        rows.append([InlineKeyboardButton(a, callback_data=f"size:{a}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back:hf")])
    return InlineKeyboardMarkup(rows)

def kb_after_prompt():
    # маленькая панель: размер/назад в меню
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📐 Размер", callback_data="menu:size")],
        [InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu:main")],
    ])

def get_lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get(K_LANG, "ru")

# -----------------------------
# HuggingFace call (router)
# -----------------------------
def hf_generate_image(model_id: str, prompt: str, width: int, height: int) -> bytes:
    """
    Calls HuggingFace router inference.
    """
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN не задан. Добавь его в Render → Environment.")

    url = f"https://router.huggingface.co/hf-inference/models/{model_id}"
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Accept": "image/png",
    }

    payload = {
        "inputs": prompt,
        "parameters": {
            "width": width,
            "height": height,
        },
    }

    r = requests.post(url, headers=headers, json=payload, timeout=180)
    if r.status_code == 200:
        return r.content

    # Постараемся показать понятную ошибку
    try:
        err = r.json()
    except Exception:
        err = {"error": r.text}

    raise RuntimeError(f"HF error {r.status_code}: {err}")

# -----------------------------
# Handlers
# -----------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[K_EXPECT_PROMPT] = False
    await update.message.reply_text(
        "Выбери язык 👇",
        reply_markup=kb_language(),
    )

async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # быстрый вызов меню без /start
    await update.message.reply_text("Главное меню 👇", reply_markup=kb_main())

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data or ""

    # -------- language
    if data.startswith("lang:"):
        code = data.split(":", 1)[1]
        context.user_data[K_LANG] = code
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("Главное меню 👇", reply_markup=kb_main())
        return

    # -------- main
    if data == "main:design":
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("🎨 Дизайн с ИИ — выбери источник:", reply_markup=kb_design())
        return

    # -------- design
    if data == "design:hf":
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("🤗 Hugging Face — выбери модель:", reply_markup=kb_hf_models())
        return

    # -------- pick model
    if data.startswith("hfmodel:"):
        model_id = data.split(":", 1)[1]
        context.user_data[K_MODEL] = model_id
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("Выбери размер (соотношение сторон):", reply_markup=kb_sizes())
        return

    # -------- pick size
    if data.startswith("size:"):
        aspect = data.split(":", 1)[1]
        context.user_data[K_ASPECT] = aspect
        context.user_data[K_EXPECT_PROMPT] = True

        model_id = context.user_data.get(K_MODEL, "")
        await q.edit_message_text(
            f"✍️ Отправь текст промпта.\n\n"
            f"Модель: {model_id}\n"
            f"Размер: {aspect}\n\n"
            f"После генерации просто пиши следующий промпт — /start больше не нужен.",
            reply_markup=kb_after_prompt(),
        )
        return

    # -------- menu shortcuts
    if data == "menu:size":
        await q.edit_message_text("Выбери размер:", reply_markup=kb_sizes())
        return

    if data == "menu:main":
        context.user_data[K_EXPECT_PROMPT] = False
        await q.edit_message_text("Главное меню 👇", reply_markup=kb_main())
        return

    # -------- back
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

    # fallback
    await q.edit_message_text("Не поняла действие. Нажми /menu", reply_markup=kb_main())

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    # если пользователь ещё не дошёл до режима промптов — просто покажем меню
    if not context.user_data.get(K_EXPECT_PROMPT, False):
        await update.message.reply_text("Нажми /start или /menu чтобы открыть меню 👇", reply_markup=kb_main())
        return

    model_id = context.user_data.get(K_MODEL)
    aspect = context.user_data.get(K_ASPECT, "1:1")

    if not model_id:
        context.user_data[K_EXPECT_PROMPT] = False
        await update.message.reply_text("Сначала выбери модель в меню 👇", reply_markup=kb_main())
        return

    w, h = ASPECTS.get(aspect, (1024, 1024))

    try:
        await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)
        # blocking call -> to thread
        img_bytes = await asyncio.to_thread(hf_generate_image, model_id, text, w, h)

        bio = io.BytesIO(img_bytes)
        bio.name = "image.png"
        bio.seek(0)

        await update.message.reply_photo(photo=bio, caption="✅ Готово!")
        # ОСТАЁМСЯ в режиме промптов (чтобы не надо было /start)
        context.user_data[K_EXPECT_PROMPT] = True

    except Exception as e:
        logger.exception("Generation failed")
        await update.message.reply_text(
            f"😕 Ошибка генерации:\n{e}\n\n"
            f"Можешь отправить другой промпт или поменять размер/модель.",
            reply_markup=kb_after_prompt(),
        )
        # остаёмся в режиме промптов
        context.user_data[K_EXPECT_PROMPT] = True

# -----------------------------
# MAIN
# -----------------------------
async def main():
    # Flask healthcheck in background thread
    flask_thread = asyncio.to_thread(run_flask)

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    await asyncio.gather(
        flask_thread,
        application.initialize(),
        application.start(),
        application.updater.start_polling(drop_pending_updates=True),
    )

if __name__ == "__main__":
    # IMPORTANT: один процесс!
    # В Render обязательно WEB_CONCURRENCY=1
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
