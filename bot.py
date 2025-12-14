import os
import io
import logging
import threading
import requests
from flask import Flask, Response

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jumpin-bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

if not TELEGRAM_TOKEN:
    raise RuntimeError("Нет TELEGRAM_TOKEN в переменных окружения Render")

# -------------------------
# Render healthcheck server
# -------------------------
flask_app = Flask(__name__)

@flask_app.get("/")
def root():
    return Response("OK", status=200)

@flask_app.get("/healthz")
def healthz():
    return Response("OK", status=200)

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)

# -------------------------
# HuggingFace inference (router)
# -------------------------
# Здесь оставляем ОДНУ дефолтную модель.
# Если захочешь поменять — меняешь только эту строку.
DEFAULT_HF_MODEL = os.getenv("HF_MODEL", "runwayml/stable-diffusion-v1-5")

RATIO_TO_SIZE = {
    "1:1": (1024, 1024),
    "9:16": (768, 1365),
    "16:9": (1365, 768),
}

def hf_generate_image(prompt: str, width: int, height: int) -> bytes:
    if not HF_TOKEN:
        raise RuntimeError("Нет HF_TOKEN в переменных окружения Render")

    url = f"https://router.huggingface.co/hf-inference/models/{DEFAULT_HF_MODEL}"

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Accept": "image/png",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": prompt,
        "parameters": {"width": width, "height": height},
    }

    r = requests.post(url, headers=headers, json=payload, timeout=180)
    if r.status_code != 200:
        try:
            msg = r.json()
        except Exception:
            msg = r.text
        raise RuntimeError(f"HF error {r.status_code}: {msg}")

    return r.content

# -------------------------
# Keyboards
# -------------------------
def kb_languages():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
         InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru")],
        [InlineKeyboardButton("🇺🇦 Українська", callback_data="lang:uk"),
         InlineKeyboardButton("🇪🇸 Español", callback_data="lang:es")],
        [InlineKeyboardButton("🇩🇪 Deutsch", callback_data="lang:de"),
         InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang:tr")],
    ])

def kb_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Дизайн с ИИ", callback_data="menu:design")],
    ])

def kb_design_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤗 Hugging Face", callback_data="design:hf")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="nav:main")],
    ])

def kb_hf_sizes():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1:1 (1024×1024)", callback_data="ratio:1:1")],
        [InlineKeyboardButton("9:16 (768×1365)", callback_data="ratio:9:16"),
         InlineKeyboardButton("16:9 (1365×768)", callback_data="ratio:16:9")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="nav:design")],
    ])

def kb_after_send():
    # Кнопка только “назад” (по твоей логике) + “размер” не обязателен,
    # но оставим "Выбрать размер" чтобы было удобно.
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📐 Размер", callback_data="nav:hf_sizes"),
         InlineKeyboardButton("⬅️ Назад", callback_data="nav:design")],
    ])

# -------------------------
# Handlers
# -------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("👋 Выбери язык:", reply_markup=kb_languages())

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data.startswith("lang:"):
        context.user_data["lang"] = data.split(":", 1)[1]
        context.user_data.pop("awaiting_prompt", None)
        await q.edit_message_text("🏠 Главное меню:", reply_markup=kb_main_menu())
        return

    if data == "menu:design":
        context.user_data.pop("awaiting_prompt", None)
        await q.edit_message_text("🎨 Дизайн с ИИ:", reply_markup=kb_design_menu())
        return

    if data == "design:hf":
        context.user_data.pop("awaiting_prompt", None)
        await q.edit_message_text("🤗 Hugging Face — выбери размер:", reply_markup=kb_hf_sizes())
        return

    if data == "nav:main":
        context.user_data.pop("awaiting_prompt", None)
        await q.edit_message_text("🏠 Главное меню:", reply_markup=kb_main_menu())
        return

    if data == "nav:design":
        context.user_data.pop("awaiting_prompt", None)
        await q.edit_message_text("🎨 Дизайн с ИИ:", reply_markup=kb_design_menu())
        return

    if data == "nav:hf_sizes":
        context.user_data.pop("awaiting_prompt", None)
        await q.edit_message_text("🤗 Hugging Face — выбери размер:", reply_markup=kb_hf_sizes())
        return

    if data.startswith("ratio:"):
        ratio = data.split(":", 1)[1]
        if ratio not in RATIO_TO_SIZE:
            await q.edit_message_text("Выбери размер из списка:", reply_markup=kb_hf_sizes())
            return

        context.user_data["ratio"] = ratio
        context.user_data["awaiting_prompt"] = True

        await q.edit_message_text(
            "✍️ Отправь текст промпта.\n\n"
            f"Размер: `{ratio}`\n"
            "После генерации просто пиши следующий промпт — /start больше не нужен.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if not context.user_data.get("awaiting_prompt"):
        await update.message.reply_text("Нажми /start чтобы открыть меню 🙂")
        return

    ratio = context.user_data.get("ratio", "1:1")
    width, height = RATIO_TO_SIZE.get(ratio, (1024, 1024))

    msg = await update.message.reply_text("⏳ Генерирую картинку...")

    try:
        img_bytes = hf_generate_image(prompt=text, width=width, height=height)

        bio = io.BytesIO(img_bytes)
        bio.name = "image.png"
        bio.seek(0)

        # Важно: оставляем awaiting_prompt=True, чтобы следующий текст сразу генерил
        context.user_data["awaiting_prompt"] = True

        await update.message.reply_photo(
            photo=bio,
            caption=f"✅ Готово!\nРазмер: `{ratio}`\n\nПиши следующий промпт 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_after_send(),
        )

        try:
            await msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.exception("Generation error")
        context.user_data["awaiting_prompt"] = True
        await msg.edit_text(
            f"😕 Ошибка генерации:\n`{e}`\n\n"
            "Можешь просто отправить другой промпт.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_after_send(),
        )

def main():
    threading.Thread(target=run_flask, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
