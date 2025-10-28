import os
import asyncio
import tempfile
import requests
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telegram import Update, Document, InputFile
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler

# ---------------------------
# Environment variables
# ---------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")                 # Telegram Bot token (BotFather)
API_ID = int(os.getenv("API_ID"))                  # from my.telegram.org
API_HASH = os.getenv("API_HASH")                   # from my.telegram.org
CLOUDWORKER = os.getenv("CLOUDWORKER")             # e.g. https://your-worker.workers.dev

if not all([BOT_TOKEN, API_ID, API_HASH, CLOUDWORKER]):
    raise RuntimeError("Set BOT_TOKEN, API_ID, API_HASH and CLOUDWORKER env vars")

# ---------------------------
# In-memory state (temporary)
# ---------------------------
# Keep temporary login clients while user completes login steps
TEMP_CLIENTS = {}   # user_id -> TelethonClient instance
PHONE_WAIT, CODE_WAIT, PASSWORD_WAIT = range(3)

# ---------------------------
# Utilities: Cloudflare worker store/get
# ---------------------------
def store_session_kv(user_id: int, session_str: str):
    url = CLOUDWORKER.rstrip("/") + "/store_session"
    payload = {"user_id": user_id, "session": session_str}
    r = requests.post(url, json=payload, timeout=15)
    return r.status_code == 200

def get_session_kv(user_id: int):
    url = CLOUDWORKER.rstrip("/") + "/get_session"
    r = requests.get(url, params={"user_id": user_id}, timeout=15)
    if r.status_code == 200:
        return r.json().get("session")
    return None

# ---------------------------
# Bot command handlers
# ---------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome — this bot lets each user log in with their Telegram account and fetch private posts.\n\n"
        "To begin, send /login\nTo fetch a private post link later, just paste the link in chat."
    )

# Step 1: begin login, ask for phone
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send your phone number in international format (example: +919812345678)."
    )
    return PHONE_WAIT

# Step 2: user sent phone, create a temp Telethon client and send code
async def phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    user_id = update.effective_user.id

    # Create a Telethon client with a blank StringSession
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except Exception as e:
        await update.message.reply_text(f"Failed to send code: {e}")
        await client.disconnect()
        return ConversationHandler.END

    # save temp client and phone
    TEMP_CLIENTS[user_id] = {"client": client, "phone": phone}
    await update.message.reply_text("Code sent! Please send the code you received (SMS/Telegram).")
    return CODE_WAIT

# Step 3: user sends code
async def code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    user_id = update.effective_user.id
    t = TEMP_CLIENTS.get(user_id)
    if not t:
        await update.message.reply_text("No pending login. Send /login to start again.")
        return ConversationHandler.END

    client: TelegramClient = t["client"]
    phone = t["phone"]
    try:
        await client.sign_in(phone=phone, code=code)
    except errors.SessionPasswordNeededError:
        await update.message.reply_text("This account has 2FA enabled. Please send your password now.")
        return PASSWORD_WAIT
    except Exception as e:
        await update.message.reply_text(f"Sign-in failed: {e}")
        await client.disconnect()
        TEMP_CLIENTS.pop(user_id, None)
        return ConversationHandler.END

    # success: save session string to Cloudflare KV
    session_str = client.session.save()
    ok = store_session_kv(user_id, session_str)
    await client.disconnect()
    TEMP_CLIENTS.pop(user_id, None)

    if ok:
        await update.message.reply_text("✅ Logged in and session saved. You can now send private post links.")
    else:
        await update.message.reply_text("⚠️ Logged in but failed to save session. Try again later.")
    return ConversationHandler.END

# Step 4: 2FA password handler
async def password_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    user_id = update.effective_user.id
    t = TEMP_CLIENTS.get(user_id)
    if not t:
        await update.message.reply_text("No pending login. Send /login to start again.")
        return ConversationHandler.END
    client: TelegramClient = t["client"]
    phone = t["phone"]
    try:
        await client.sign_in(password=password)
    except Exception as e:
        await update.message.reply_text(f"2FA sign-in failed: {e}")
        await client.disconnect()
        TEMP_CLIENTS.pop(user_id, None)
        return ConversationHandler.END

    session_str = client.session.save()
    ok = store_session_kv(user_id, session_str)
    await client.disconnect()
    TEMP_CLIENTS.pop(user_id, None)

    if ok:
        await update.message.reply_text("✅ Logged in and session saved. You can now send private post links.")
    else:
        await update.message.reply_text("⚠️ Logged in but failed to save session. Try again later.")
    return ConversationHandler.END

# Cancel
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    t = TEMP_CLIENTS.pop(user_id, None)
    if t:
        try:
            await t["client"].disconnect()
        except: pass
    await update.message.reply_text("Login cancelled.")
    return ConversationHandler.END

# ---------------------------
# Handler: when user sends a link (fetch & forward)
# ---------------------------
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Accept common private post link formats: https://t.me/c/<chat>/<msgid> or https://t.me/<username>/<msgid>
    if "t.me" not in text or "/" not in text:
        return  # ignore non-links

    # fetch stored session string for this user
    session_str = get_session_kv(user_id)
    if not session_str:
        await update.message.reply_text("You are not logged in. Send /login to start.")
        return

    # Create Telethon client from saved session
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await client.connect()

    # parse link to get chat and msg id
    try:
        # handle t.me/c/<chat_id>/<msgid>
        if "/c/" in text:
            # example: https://t.me/c/123456789/42  -> chat id = -100123456789
            parts = text.rstrip("/").split("/")
            msg_id = int(parts[-1])
            raw_chat = parts[-2]  # numeric chat id without -100
            chat_id = int(f"-100{raw_chat}")
        else:
            # public link or username form: t.me/username/42
            parts = text.rstrip("/").split("/")
            username = parts[-2]
            msg_id = int(parts[-1])
            # Telethon accepts username
            chat_id = username

        msg = await client.get_messages(chat_id, ids=msg_id)
        if not msg:
            await update.message.reply_text("Message not found or I don't have access.")
            await client.disconnect()
            return

        # If message has media, download temp and send via Bot
        if msg.media:
            tmp = await client.download_media(msg, file= tempfile.gettempdir())
            # choose send method based on file type by letting python-telegram-bot decide
            # If it's an image/video/document, use send_document which works for all
            await update.message.reply_document(document=InputFile(tmp), caption=msg.text or msg.caption or "")
        else:
            # text only
            await update.message.reply_text(msg.text or msg.message or "No text found.")

        await client.disconnect()
    except Exception as e:
        try:
            await client.disconnect()
        except: pass
        await update.message.reply_text(f"Failed to fetch: {e}")

# ---------------------------
# Setup bot & conversation handler
# ---------------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('login', login_start)],
        states = {
            PHONE_WAIT: [MessageHandler(filters.TEXT & (~filters.COMMAND), phone_received)],
            CODE_WAIT: [MessageHandler(filters.TEXT & (~filters.COMMAND), code_received)],
            PASSWORD_WAIT: [MessageHandler(filters.TEXT & (~filters.COMMAND), password_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(CommandHandler('start', start))
    app.add_handler(conv_handler)
    # link handler - simple: any text containing t.me will be attempted
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_link))

    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
