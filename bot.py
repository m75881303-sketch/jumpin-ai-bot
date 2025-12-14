import os
import json
import threading
from io import BytesIO

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
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or os.getenv("TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
HF_MODEL = os.getenv("HF_MODEL") or "stabilityai/stable-diffusion-xl-base-1.0"

# HF Router (важно!)
HF_URL = f"https://router.huggingface.co/hf-inference/models/{HF_MODEL}"

if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN (или TELEGRAM_TOKEN/TOKEN) в переменных окружения")

if not HF_TOKEN:
    raise RuntimeError("Нет HF_TOKEN в переменных окружения Render")

# =========================
# Flask healthcheck for Render Web Service
# =========================
app = Flask(__name__)

@app.get("/healthz")
def healthz():
    return "ok", 200

def run_flask():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

# =========================
# UI TEXTS
# =========================
TXT = {
    "ru": {
        "choose_lang": "Пожалуйста, выбери язык:",
        "main_menu": "🏠 Главное меню\nВыбери раздел 👇",
        "design_menu": "🎨 Дизайн с ИИ\nВыбери провайдера 👇",
        "hf_menu": "🤗 Hugging Face\nВыбери размер 👇",
        "send_prompt": "✍️ Отправь текст промпта.\n\nРазмер: {ratio}\nПосле генерации просто пиши следующий промпт — /start не нужен.",
        "generating": "Генерирую картинку… ⏳",
        "done": "✅ Готово!",
        "err_generic": "😕 Ошибка генерации:\n{msg}\n\nМожешь просто отправить другой промпт.",
        "hf_404": "❌ HF 404: модель/эндпоинт не найден.\nПроверь HF_MODEL и что URL — router.huggingface.co",
        "hf_403": "❌ HF 403: у токена не хватает прав.\nНужно выдать доступ в Hugging Face (разрешение на Inference Providers).",
        "back": "⬅️ Назад",
        "size": "📐 Размер",
        "hf": "Hugging Face",
        "design_ai": "🎨 Дизайн с ИИ",
    },
    "en": {
        "choose_lang": "Please choose a language:",
        "main_menu": "🏠 Main menu\nChoose a section 👇",
        "design_menu": "🎨 AI Design\nChoose provider 👇",
        "hf_menu": "🤗 Hugging Face\nChoose size 👇",
        "send_prompt": "✍️ Send your prompt text.\n\nSize: {ratio}\nAfter generation just send the next prompt — /start is not needed.",
        "generating": "Generating image… ⏳",
        "done": "✅ Done!",
        "err_generic": "😕 Generation error:\n{msg}\n\nYou can just send another prompt.",
        "hf_404": "❌ HF 404: model/endpoint not found.\nCheck HF_MODEL and router.huggingface.co URL.",
        "hf_403": "❌ HF 403: token has insufficient permissions.\nEnable permissions for Inference Providers in Hugging Face token/app.",
        "back": "⬅️ Back",
        "size": "📐 Size",
        "hf": "Hugging Face",
        "design_ai": "🎨 AI Design",
    }
}

# =========================
# Helpers: user state
# =========================
DEFAULT_RATIO = "16:9"
RATIOS = {
    "1:1": (1024, 1024),
    "9:16": (768, 1344),
    "16:9": (1344, 768),
}

def get_lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang", "ru")

def t(context: ContextTypes.DEFAULT_TYPE, key: str) -> str:
    return TXT[get_lang(context)][key]

def set_ratio(context: ContextTypes.DEFAULT_TYPE, ratio: str):
    if ratio not in RATIOS:
        ratio = DEFAULT_RATIO
    context.user_data["ratio"] = ratio
    context.user_data["width"], context.user_data["height"] = RATIOS[ratio]

def get_ratio(context: ContextTypes.DEFAULT_TYPE) -> str:
    ratio = context.user_data.get("ratio")
    if not ratio:
        set_ratio(context, DEFAULT_RATIO)
        ratio = DEFAULT_RATIO
    return ratio

# =========================
# Menus (inline keyboards)
# =========================
def kb_language():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇷🇺 Русский", callback_data="LANG:ru"),
            InlineKeyboardButton("🇬🇧 English", callback_data="LANG:en"),
        ]
    ])

def kb_main(context: ContextTypes.DEFAULT_TYPE):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(TXT[get_lang(context)]["design_ai"], callback_data="MENU:DESIGN")]
    ])

def kb_design(context: ContextTypes.DEFAULT_TYPE):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(TXT[get_lang(context)]["hf"], callback_data="DESIGN:HF")],
        [InlineKeyboardButton(TXT[get_lang(context)]["back"], callback_data="NAV:MAIN")],
    ])

def kb_hf_sizes(context: ContextTypes.DEFAULT_TYPE):
    # порядок как у тебя: 1:1 / 9:16 / 16:9
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1:1", callback_data="SIZE:1:1"),
            InlineKeyboardButton("9:16", callback_data="SIZE:9:16"),
            InlineKeyboardButton("16:9", callback_data="SIZE:16:9"),
        ],
        [InlineKeyboardButton(TXT[get_lang(context)]["back"], callback_data="NAV:DESIGN")],
    ])

def kb_after_gen(context: ContextTypes.DEFAULT_TYPE):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(TXT[get_lang(context)]["size"], callback_data="NAV:SIZE"),
            InlineKeyboardButton(TXT[get_lang(context)]["back"], callback_data="NAV:DESIGN"),
        ]
    ])

# =========================
# HF image generation
# =========================
def hf_generate_image(prompt: str, width: int, height: int) -> bytes:
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "image/png",
    }

    payload = {
        "inputs": prompt,
        "parameters": {
            "width": width,
            "height": height,
            "num_inference_steps": 30,
            "guidance_scale": 7.5
        }
    }

    resp = requests.post(HF_URL, headers=headers, json=payload, timeout=120)

    # Часто HF возвращает JSON с ошибкой
    ctype = resp.headers.get("content-type", "")

    if resp.status_code == 404:
        raise RuntimeError("HF_404")
    if resp.status_code == 403:
        raise RuntimeError("HF_403")

    if "application/json" in ctype:
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"HF error {resp.status_code}: {resp.text[:500]}")
        # HuggingFace часто кладёт ошибку в поле "error"
        err = data.get("error") or data
        raise RuntimeError(f"HF error {resp.status_code}: {err}")

    if resp.status_code >= 400:
        raise RuntimeError(f"HF error {resp.status_code}: {resp.text[:500]}")

    return resp.content

# =========================
# Handlers
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /start всегда открывает выбор языка, как ты хотела
    context.user_data.clear()
    set_ratio(context, DEFAULT_RATIO)
    await update.message.reply_text(TXT["ru"]["choose_lang"], reply_markup=kb_language())

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # LANG
    if data.startswith("LANG:"):
        lang = data.split(":", 1)[1]
        context.user_data["lang"] = lang if lang in ("ru", "en") else "ru"
        # после выбора языка -> главное меню
        await query.edit_message_text(t(context, "main_menu"), reply_markup=kb_main(context))
        return

    # NAV
    if data == "NAV:MAIN":
        await query.edit_message_text(t(context, "main_menu"), reply_markup=kb_main(context))
        return

    if data == "MENU:DESIGN" or data == "NAV:DESIGN":
        await query.edit_message_text(t(context, "design_menu"), reply_markup=kb_design(context))
        return

    if data == "DESIGN:HF":
        await query.edit_message_text(t(context, "hf_menu"), reply_markup=kb_hf_sizes(context))
        return

    if data == "NAV:SIZE":
        # просто открыть выбор размеров (без /start)
        await query.edit_message_text(t(context, "hf_menu"), reply_markup=kb_hf_sizes(context))
        return

    # SIZE
    if data.startswith("SIZE:"):
        ratio = data.split(":", 1)[1]
        set_ratio(context, ratio)
        context.user_data["awaiting_prompt"] = True  # теперь просто ждем текст, без /start после генерации
        await query.edit_message_text(
            t(context, "send_prompt").format(ratio=get_ratio(context)),
            reply_markup=kb_after_gen(context)
        )
        return

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    if not msg:
        return

    # если человек ещё не выбрал язык — отправляем выбор языка
    if "lang" not in context.user_data:
        set_ratio(context, DEFAULT_RATIO)
        await update.message.reply_text(TXT["ru"]["choose_lang"], reply_markup=kb_language())
        return

    # если НЕ в режиме ожидания промпта — открываем главное меню
    if not context.user_data.get("awaiting_prompt"):
        await update.message.reply_text(t(context, "main_menu"), reply_markup=kb_main(context))
        return

    # Генерация (HF)
    ratio = get_ratio(context)
    width = context.user_data.get("width", RATIOS[ratio][0])
    height = context.user_data.get("height", RATIOS[ratio][1])

    await update.message.chat.send_action(action=ChatAction.UPLOAD_PHOTO)
    await update.message.reply_text(t(context, "generating"))

    try:
        img_bytes = hf_generate_image(msg, width, height)
        bio = BytesIO(img_bytes)
        bio.name = "image.png"
        bio.seek(0)

        await update.message.reply_photo(photo=bio, caption=t(context, "done"))
        # остаёмся в режиме ожидания промпта (чтобы /start не нужен)
        context.user_data["awaiting_prompt"] = True
        await update.message.reply_text(
            t(context, "send_prompt").format(ratio=get_ratio(context)),
            reply_markup=kb_after_gen(context)
        )

    except RuntimeError as e:
        code = str(e)

        if code == "HF_404":
            await update.message.reply_text(t(context, "hf_404"), reply_markup=kb_after_gen(context))
            return
        if code == "HF_403":
            await update.message.reply_text(t(context, "hf_403"), reply_markup=kb_after_gen(context))
            return

        await update.message.reply_text(
            t(context, "err_generic").format(msg=code),
            reply_markup=kb_after_gen(context)
        )

# =========================
# MAIN
# =========================
def run_bot():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Важно: polling. На Render держи Scaling=1, иначе будет Conflict getUpdates.
    application.run_polling(close_loop=False)

if __name__ == "__main__":
    # Flask для /healthz + Telegram polling в фоне
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    run_flask()
