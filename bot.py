import os
import io
import json
import time
import threading
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

# =========================
# ENV VARS (Render -> Environment)
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")  # HuggingFace token
HF_MODEL_DEFAULT = os.getenv("HF_MODEL", "stabilityai/stable-diffusion-2-1")

# IMPORTANT: use HF router base
HF_BASE_URL = os.getenv("HF_BASE_URL", "https://router.huggingface.co/hf-inference")

# Render port for health check
PORT = int(os.getenv("PORT", "10000"))

if not TELEGRAM_TOKEN:
    raise RuntimeError("Нет TELEGRAM_TOKEN (или TOKEN) в переменных окружения Render")

# =========================
# Minimal web server for Render health checks
# =========================
app = Flask(__name__)

@app.get("/")
def root():
    return "ok", 200

@app.get("/healthz")
def healthz():
    return "ok", 200

def run_health_server():
    # Render expects binding to 0.0.0.0:PORT
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# =========================
# UI / i18n
# =========================
LANGS = [
    ("en", "🇬🇧 English"),
    ("ru", "🇷🇺 Русский"),
]

TEXT: Dict[str, Dict[str, str]] = {
    "ru": {
        "choose_lang": "Пожалуйста, выберите язык:",
        "main_menu": "🏠 Главное меню\nВыберите нужный раздел 👇",
        "design_menu": "🎨 Дизайн с ИИ\nВыберите раздел для работы с изображением 👇",
        "hf_menu": "🤗 Hugging Face\nВыберите размер изображения 👇",
        "prompt_intro": "✍️ Отправь текст промпта.\n\nРазмер: {ratio}\nПосле генерации просто пиши следующий промпт — /start больше не нужен.",
        "generating": "Генерирую картинку... ⏳",
        "no_hf_token": "❌ Нет HF_TOKEN в Render Environment Variables.\nДобавь HF_TOKEN и сделай redeploy.",
        "hf_403": "❌ HF 403: у токена нет прав на Inference.\nСоздай новый токен на Hugging Face и включи галочку **Inference → Make calls to Inference Providers**.",
        "hf_404": "❌ HF 404: модель/эндпоинт не найден.\nПроверь HF_MODEL и базовый URL.",
        "hf_other": "❌ Ошибка генерации:\n{msg}",
        "btn_design": "🎨 Дизайн с ИИ",
        "btn_hf": "🤗 Hugging Face",
        "btn_back": "⬅️ Назад",
        "btn_size": "📐 Размер",
        "btn_ratio_11": "1:1",
        "btn_ratio_916": "9:16",
        "btn_ratio_169": "16:9",
    },
    "en": {
        "choose_lang": "Please choose a language:",
        "main_menu": "🏠 Main menu\nChoose an option 👇",
        "design_menu": "🎨 AI Design\nChoose image tool 👇",
        "hf_menu": "🤗 Hugging Face\nChoose image size 👇",
        "prompt_intro": "✍️ Send a prompt.\n\nSize: {ratio}\nAfter generation, just send the next prompt — /start is not needed.",
        "generating": "Generating image... ⏳",
        "no_hf_token": "❌ No HF_TOKEN in Render Environment Variables.\nAdd HF_TOKEN and redeploy.",
        "hf_403": "❌ HF 403: token has no Inference permissions.\nCreate a new token on Hugging Face and enable **Inference → Make calls to Inference Providers**.",
        "hf_404": "❌ HF 404: model/endpoint not found.\nCheck HF_MODEL and base URL.",
        "hf_other": "❌ Generation error:\n{msg}",
        "btn_design": "🎨 AI Design",
        "btn_hf": "🤗 Hugging Face",
        "btn_back": "⬅️ Back",
        "btn_size": "📐 Size",
        "btn_ratio_11": "1:1",
        "btn_ratio_916": "9:16",
        "btn_ratio_169": "16:9",
    },
}

def get_lang(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    return ctx.user_data.get("lang", "ru")

def t(ctx: ContextTypes.DEFAULT_TYPE, key: str, **kwargs) -> str:
    lang = get_lang(ctx)
    s = TEXT.get(lang, TEXT["ru"]).get(key, key)
    return s.format(**kwargs) if kwargs else s

# =========================
# Menus (Inline Keyboards)
# =========================
def kb_lang() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"lang:{code}")]
        for code, label in LANGS
    ])

def kb_main(ctx: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(ctx, "btn_design"), callback_data="menu:design")]
    ])

def kb_design(ctx: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(ctx, "btn_hf"), callback_data="design:hf")],
        [InlineKeyboardButton(t(ctx, "btn_back"), callback_data="back:main")],
    ])

def kb_hf_sizes(ctx: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(ctx, "btn_ratio_11"), callback_data="size:1:1"),
            InlineKeyboardButton(t(ctx, "btn_ratio_916"), callback_data="size:9:16"),
            InlineKeyboardButton(t(ctx, "btn_ratio_169"), callback_data="size:16:9"),
        ],
        [InlineKeyboardButton(t(ctx, "btn_back"), callback_data="back:design")],
    ])

def kb_prompt_controls(ctx: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(ctx, "btn_size"), callback_data="prompt:size"),
            InlineKeyboardButton(t(ctx, "btn_back"), callback_data="back:hf"),
        ]
    ])

# =========================
# Size mapping
# =========================
RATIO_TO_WH: Dict[str, Tuple[int, int]] = {
    "1:1": (1024, 1024),
    "9:16": (768, 1344),
    "16:9": (1344, 768),
}

def set_mode(ctx: ContextTypes.DEFAULT_TYPE, mode: str):
    ctx.user_data["mode"] = mode

def get_mode(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    return ctx.user_data.get("mode", "idle")

def set_ratio(ctx: ContextTypes.DEFAULT_TYPE, ratio: str):
    ctx.user_data["ratio"] = ratio

def get_ratio(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    return ctx.user_data.get("ratio", "1:1")

def set_model(ctx: ContextTypes.DEFAULT_TYPE, model: str):
    ctx.user_data["hf_model"] = model

def get_model(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    return ctx.user_data.get("hf_model", HF_MODEL_DEFAULT)

# =========================
# HF call
# =========================
def hf_text_to_image(prompt: str, model: str, width: int, height: int) -> bytes:
    """
    Calls Hugging Face router inference API and returns image bytes.
    """
    url = f"{HF_BASE_URL}/models/{model}"
    headers = {"Accept": "image/png"}
    if HF_TOKEN:
        headers["Authorization"] = f"Bearer {HF_TOKEN}"

    payload = {
        "inputs": prompt,
        "parameters": {
            "width": width,
            "height": height,
            "num_inference_steps": 28,
            "guidance_scale": 7.0,
        }
    }

    r = requests.post(url, headers=headers, json=payload, timeout=180)

    # Image success
    ctype = (r.headers.get("content-type") or "").lower()
    if r.ok and ("image/" in ctype or r.content[:8] == b"\x89PNG\r\n\x1a\n"):
        return r.content

    # Otherwise error
    try:
        data = r.json()
    except Exception:
        data = {"error": r.text}

    code = r.status_code
    msg = json.dumps(data, ensure_ascii=False)

    # Common HF "model loading" 503 case
    if code == 503 and isinstance(data, dict):
        # Sometimes it contains {"error":"Model ... is currently loading", "estimated_time":...}
        est = data.get("estimated_time")
        if est:
            # small wait once and retry
            time.sleep(min(12, float(est)))
            r2 = requests.post(url, headers=headers, json=payload, timeout=180)
            ctype2 = (r2.headers.get("content-type") or "").lower()
            if r2.ok and ("image/" in ctype2 or r2.content[:8] == b"\x89PNG\r\n\x1a\n"):
                return r2.content
            try:
                data2 = r2.json()
            except Exception:
                data2 = {"error": r2.text}
            raise RuntimeError(f"HF error {r2.status_code}: {json.dumps(data2, ensure_ascii=False)}")

    raise RuntimeError(f"HF error {code}: {msg}")

# =========================
# Handlers
# =========================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Always start from language selection
    set_mode(ctx, "lang")
    await update.message.reply_text(t(ctx, "choose_lang"), reply_markup=kb_lang())

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    # Language chosen
    if data.startswith("lang:"):
        code = data.split(":", 1)[1]
        ctx.user_data["lang"] = code
        set_mode(ctx, "main")
        await q.edit_message_text(t(ctx, "main_menu"), reply_markup=kb_main(ctx))
        return

    # Menus
    if data == "menu:design":
        set_mode(ctx, "design")
        await q.edit_message_text(t(ctx, "design_menu"), reply_markup=kb_design(ctx))
        return

    if data == "design:hf":
        set_mode(ctx, "hf")
        await q.edit_message_text(t(ctx, "hf_menu"), reply_markup=kb_hf_sizes(ctx))
        return

    # Back navigation
    if data == "back:main":
        set_mode(ctx, "main")
        await q.edit_message_text(t(ctx, "main_menu"), reply_markup=kb_main(ctx))
        return

    if data == "back:design":
        set_mode(ctx, "design")
        await q.edit_message_text(t(ctx, "design_menu"), reply_markup=kb_design(ctx))
        return

    if data == "back:hf":
        set_mode(ctx, "hf")
        await q.edit_message_text(t(ctx, "hf_menu"), reply_markup=kb_hf_sizes(ctx))
        return

    # Size chosen
    if data.startswith("size:"):
        ratio = data.split(":", 1)[1]
        if ratio not in RATIO_TO_WH:
            ratio = "1:1"
        set_ratio(ctx, ratio)
        set_mode(ctx, "await_prompt")
        await q.edit_message_text(
            t(ctx, "prompt_intro", ratio=ratio),
            reply_markup=kb_prompt_controls(ctx),
        )
        return

    # While prompting: open size picker
    if data == "prompt:size":
        set_mode(ctx, "hf")  # show sizes screen (but we will keep generating mode after pick)
        await q.edit_message_text(t(ctx, "hf_menu"), reply_markup=kb_hf_sizes(ctx))
        return

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # If user types something but not in prompt mode -> open main menu (or language if missing)
    mode = get_mode(ctx)

    if mode in ("idle", "lang"):
        set_mode(ctx, "lang")
        await update.message.reply_text(t(ctx, "choose_lang"), reply_markup=kb_lang())
        return

    if mode in ("main", "design", "hf"):
        # They typed text instead of pressing buttons: show the current menu again
        if mode == "main":
            await update.message.reply_text(t(ctx, "main_menu"), reply_markup=kb_main(ctx))
        elif mode == "design":
            await update.message.reply_text(t(ctx, "design_menu"), reply_markup=kb_design(ctx))
        else:
            await update.message.reply_text(t(ctx, "hf_menu"), reply_markup=kb_hf_sizes(ctx))
        return

    # Prompt mode
    prompt = (update.message.text or "").strip()
    if not prompt:
        return

    if not HF_TOKEN:
        await update.message.reply_text(t(ctx, "no_hf_token"))
        return

    ratio = get_ratio(ctx)
    width, height = RATIO_TO_WH.get(ratio, (1024, 1024))
    model = get_model(ctx)

    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
    status_msg = await update.message.reply_text(t(ctx, "generating"))

    try:
        img_bytes = hf_text_to_image(prompt=prompt, model=model, width=width, height=height)
        bio = io.BytesIO(img_bytes)
        bio.name = "image.png"
        bio.seek(0)

        # Send photo and keep prompt mode (no /start needed)
        await update.message.reply_photo(photo=bio, caption="✅ Готово!" if get_lang(ctx) == "ru" else "✅ Done!")
        await status_msg.delete()

        # Keep mode as await_prompt, show quick controls
        set_mode(ctx, "await_prompt")
        await update.message.reply_text(
            t(ctx, "prompt_intro", ratio=ratio),
            reply_markup=kb_prompt_controls(ctx),
        )

    except RuntimeError as e:
        err = str(e)
        await status_msg.delete()

        if "HF error 403" in err:
            await update.message.reply_text(t(ctx, "hf_403"))
        elif "HF error 404" in err:
            await update.message.reply_text(t(ctx, "hf_404"))
        else:
            await update.message.reply_text(t(ctx, "hf_other", msg=err))

        # stay in prompt mode so they can retry quickly
        set_mode(ctx, "await_prompt")

# Optional: /menu to open main menu without resetting language
async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if "lang" not in ctx.user_data:
        set_mode(ctx, "lang")
        await update.message.reply_text(t(ctx, "choose_lang"), reply_markup=kb_lang())
        return
    set_mode(ctx, "main")
    await update.message.reply_text(t(ctx, "main_menu"), reply_markup=kb_main(ctx))

def main():
    # Start health server thread
    th = threading.Thread(target=run_health_server, daemon=True)
    th.start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_cmd))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # IMPORTANT:
    # Conflict "terminated by other getUpdates request" happens if you run the bot in 2 places.
    # Make sure ONLY Render is running (no local polling).
    application.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
