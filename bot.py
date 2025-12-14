import os
import asyncio
import logging
import threading
from io import BytesIO

import aiohttp
from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -------------------------
# CONFIG
# -------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("jump-bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")  # Hugging Face token (Read / Inference permissions)

# Один выбранный HF-модельный эндпоинт (без меню моделей — как ты просила)
# Если захочешь поменять модель — меняешь только тут:
HF_MODEL = os.getenv("HF_MODEL", "runwayml/stable-diffusion-v1-5")

# Новый router endpoint (api-inference больше не поддерживается)
HF_URL = f"https://router.huggingface.co/hf-inference/models/{HF_MODEL}"

PORT = int(os.getenv("PORT", "10000"))

# -------------------------
# SMALL WEB SERVER (Render needs open port)
# -------------------------
web_app = Flask(__name__)

@web_app.get("/")
def root():
    return "OK", 200

@web_app.get("/healthz")
def healthz():
    return "OK", 200

def run_web():
    # host must be 0.0.0.0 for Render
    web_app.run(host="0.0.0.0", port=PORT)

# -------------------------
# UI TEXTS
# -------------------------
TEXT = {
    "ru": {
        "choose_lang": "Пожалуйста, выбери язык:",
        "main_menu": "🏠 *Главное меню*\nВыбери раздел 👇",
        "ai_design": "🎨 *Дизайн с ИИ*\nВыбери провайдера 👇",
        "choose_provider": "Выбери провайдера 👇",
        "choose_ratio": "Выбери размер изображения 👇",
        "send_prompt": "✍️ Отправь текст промпта.\n\n*Размер:* {ratio}\n\nПосле генерации просто пиши следующий промпт — /start больше не нужен.",
        "generating": "⏳ Генерирую картинку…",
        "error_prefix": "Ошибка генерации 😕\n\n",
        "need_token": "Не найден HF_TOKEN. Добавь его в Render → Environment Variables.",
        "back": "⬅️ Назад",
        "provider_hf": "🤗 Hugging Face",
        "menu_ai": "🎨 Дизайн с ИИ",
        "ratio_1_1": "1:1",
        "ratio_9_16": "9:16",
        "ratio_16_9": "16:9",
        "hint_menu": "Чтобы открыть меню — нажми /start 🙂",
    },
    "en": {
        "choose_lang": "Please choose a language:",
        "main_menu": "🏠 *Main menu*\nChoose a section 👇",
        "ai_design": "🎨 *AI Design*\nChoose a provider 👇",
        "choose_provider": "Choose a provider 👇",
        "choose_ratio": "Choose image size 👇",
        "send_prompt": "✍️ Send your prompt text.\n\n*Size:* {ratio}\n\nAfter generation just send the next prompt — no need for /start.",
        "generating": "⏳ Generating image…",
        "error_prefix": "Generation error 😕\n\n",
        "need_token": "HF_TOKEN is missing. Add it in Render → Environment Variables.",
        "back": "⬅️ Back",
        "provider_hf": "🤗 Hugging Face",
        "menu_ai": "🎨 AI Design",
        "ratio_1_1": "1:1",
        "ratio_9_16": "9:16",
        "ratio_16_9": "16:9",
        "hint_menu": "To open menu — send /start 🙂",
    },
}

def get_lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang", "ru")

# -------------------------
# KEYBOARDS
# -------------------------
def kb_lang():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang:en")],
    ])

def kb_main(lang: str):
    t = TEXT[lang]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t["menu_ai"], callback_data="menu:ai")],
    ])

def kb_ai_design(lang: str):
    t = TEXT[lang]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t["provider_hf"], callback_data="provider:hf")],
        [InlineKeyboardButton(t["back"], callback_data="back:main")],
    ])

def kb_ratio(lang: str):
    t = TEXT[lang]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t["ratio_1_1"], callback_data="ratio:1:1"),
            InlineKeyboardButton(t["ratio_9_16"], callback_data="ratio:9:16"),
            InlineKeyboardButton(t["ratio_16_9"], callback_data="ratio:16:9"),
        ],
        [InlineKeyboardButton(t["back"], callback_data="back:provider")],
    ])

# -------------------------
# COMMANDS
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /start всегда открывает выбор языка (как у тебя на скринах)
    await update.message.reply_text(TEXT["ru"]["choose_lang"], reply_markup=kb_lang())

# -------------------------
# CALLBACKS (buttons)
# -------------------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    # log.info("callback: %s", data)

    if data.startswith("lang:"):
        lang = data.split(":", 1)[1]
        context.user_data["lang"] = lang
        # не чистим весь user_data — чтобы не ломать режим
        await query.edit_message_text(
            TEXT[lang]["main_menu"],
            reply_markup=kb_main(lang),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lang = get_lang(context)
    t = TEXT[lang]

    if data == "menu:ai":
        await query.edit_message_text(
            t["ai_design"],
            reply_markup=kb_ai_design(lang),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "provider:hf":
        # включаем режим HF (без меню моделей)
        context.user_data["mode"] = "hf"
        await query.edit_message_text(
            t["choose_ratio"],
            reply_markup=kb_ratio(lang),
        )
        return

    if data.startswith("ratio:"):
        # формат callback_data: ratio:1:1 или ratio:9:16 или ratio:16:9
        ratio = data.split(":", 1)[1]
        context.user_data["ratio"] = ratio
        context.user_data["mode"] = "hf"
        context.user_data["awaiting_prompt"] = True

        await query.edit_message_text(
            t["send_prompt"].format(ratio=ratio),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "back:main":
        await query.edit_message_text(
            t["main_menu"],
            reply_markup=kb_main(lang),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "back:provider":
        await query.edit_message_text(
            t["ai_design"],
            reply_markup=kb_ai_design(lang),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

# -------------------------
# IMAGE GENERATION (HF router)
# -------------------------
def ratio_to_size(ratio: str) -> tuple[int, int]:
    # стабильные размеры (кратные 8), чтобы SD не ругался
    # 1:1 => 512x512
    # 9:16 => 512x912
    # 16:9 => 912x512
    if ratio == "9:16":
        return (512, 912)
    if ratio == "16:9":
        return (912, 512)
    return (512, 512)

async def generate_hf_image_bytes(prompt: str, ratio: str) -> bytes:
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN_MISSING")

    width, height = ratio_to_size(ratio)

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Accept": "image/png",
    }

    payload = {
        "inputs": prompt,
        "parameters": {
            "width": width,
            "height": height,
            # можно добавить шаги/гиданс, если захочешь:
            # "num_inference_steps": 25,
            # "guidance_scale": 7.0,
        }
    }

    timeout = aiohttp.ClientTimeout(total=120)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(HF_URL, headers=headers, json=payload) as resp:
            ct = resp.headers.get("content-type", "")
            body = await resp.read()

            # Успех — вернулся бинарный image/*
            if resp.status == 200 and ct.startswith("image/"):
                return body

            # Ошибка — обычно JSON
            try:
                text = body.decode("utf-8", errors="ignore")
            except Exception:
                text = str(body)

            raise RuntimeError(f"HF error {resp.status}: {text}")

async def generate_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    lang = get_lang(context)
    t = TEXT[lang]

    ratio = context.user_data.get("ratio") or "1:1"

    if not HF_TOKEN:
        await update.message.reply_text(t["need_token"])
        return

    msg = await update.message.reply_text(t["generating"])

    try:
        img_bytes = await generate_hf_image_bytes(prompt=prompt, ratio=ratio)
        bio = BytesIO(img_bytes)
        bio.name = "image.png"
        bio.seek(0)

        await update.message.reply_photo(photo=bio, caption="✅ Готово!")
        # режим НЕ сбрасываем → можно слать следующий промпт сразу
        context.user_data["mode"] = "hf"
        context.user_data["awaiting_prompt"] = True

    except Exception as e:
        err = str(e)
        if "HF_TOKEN_MISSING" in err:
            err = t["need_token"]
        await update.message.reply_text(t["error_prefix"] + err)
    finally:
        # удаляем "генерирую..." чтобы не захламлять
        try:
            await msg.delete()
        except Exception:
            pass

# -------------------------
# TEXT HANDLER
# -------------------------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    # команды не считаем промптом
    if text.startswith("/"):
        return

    mode = context.user_data.get("mode")
    ratio = context.user_data.get("ratio")

    # ✅ Главный фикс: если выбран HF + размер, то ЛЮБОЙ текст = промпт
    if mode == "hf" and ratio:
        await generate_and_send(update, context, prompt=text)
        return

    # запасной вариант (если вдруг ratio ещё не выбрали)
    if context.user_data.get("awaiting_prompt"):
        await generate_and_send(update, context, prompt=text)
        return

    lang = get_lang(context)
    await update.message.reply_text(TEXT[lang]["hint_menu"])

# -------------------------
# MAIN
# -------------------------
def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Нет TELEGRAM_TOKEN (или TOKEN) в переменных окружения.")

    # запускаем web (порт) в отдельном потоке — Render будет счастлив
    threading.Thread(target=run_web, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Важно: ровно 1 инстанс на Render, иначе будет Conflict getUpdates
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
