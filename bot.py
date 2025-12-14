import os
import threading
from flask import Flask

app = Flask(__name__)

@app.get("/")
def home():
    return "ok", 200

def run_web():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
    import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎨 Арт", callback_data="art")]
    ]
    await update.message.reply_text(
        "Выбери категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def on_art(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("Отправь текст промпта")
    context.user_data["await_prompt"] = True

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_prompt"):
        return

    prompt = update.message.text
    context.user_data["await_prompt"] = False
    await update.message.reply_text(
        f"Принято ✅\n\nПромпт:\n{prompt}\n\n(Дальше подключим нейросети по API)"
    )

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_art, pattern="art"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling()

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_web, daemon=True).start()
    main()
