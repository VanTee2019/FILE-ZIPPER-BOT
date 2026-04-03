import os
import zipfile
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TMP_DIR = "/tmp"

COLLECTING = 1
NAMING = 2

user_files = {}


def user_dir(uid):
    p = os.path.join(TMP_DIR, f"zb_{uid}")
    os.makedirs(p, exist_ok=True)
    return p


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆕 ZIP BOT v3 READY\n\n"
        "Send me files one by one.\n"
        "When done, send /zip and I will ask you to NAME the ZIP before compressing.\n\n"
        "/zip - compress files\n"
        "/list - see queued files\n"
        "/clear - remove all files"
    )
    return COLLECTING


async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message

    f = None
    name = None

    if msg.document:
        f = msg.document
        name = f.file_name or f"doc_{f.file_id[:6]}"
    elif msg.audio:
        f = msg.audio
        name = f.file_name or f"audio_{f.file_id[:6]}.mp3"
    elif msg.video:
        f = msg.video
        name = f.file_name or f"video_{f.file_id[:6]}.mp4"
    elif msg.photo:
        f = msg.photo[-1]
        name = f"photo_{f.file_id[:6]}.jpg"
    elif msg.voice:
        f = msg.voice
        name = f"voice_{f.file_id[:6]}.ogg"
    else:
        await msg.reply_text("⚠️ Send documents, audio, video or photos only.")
        return COLLECTING

    path = os.path.join(user_dir(uid), name)
    fo = await context.bot.get_file(f.file_id)
    await fo.download_to_drive(path)

    if uid not in user_files:
        user_files[uid] = []
    user_files[uid].append({"name": name, "path": path})
    n = len(user_files[uid])

    await msg.reply_text(
        f"✅ {name} saved! ({n} file(s) queued)\n\nSend more files or /zip to compress."
    )
    return COLLECTING


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    files = user_files.get(uid, [])
    if not files:
        await update.message.reply_text("📭 No files queued.")
        return COLLECTING
    lines = "\n".join(f"{i+1}. {f['name']}" for i, f in enumerate(files))
    await update.message.reply_text(f"📋 Queued ({len(files)}):\n{lines}")
    return COLLECTING


async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    for f in user_files.get(uid, []):
        try: os.remove(f["path"])
        except: pass
    user_files[uid] = []
    await update.message.reply_text("🗑️ Cleared!")
    return COLLECTING


async def zip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    files = user_files.get(uid, [])

    if not files:
        await update.message.reply_text("📭 No files yet! Send files first then /zip.")
        return COLLECTING

    lines = "\n".join(f"{i+1}. {f['name']}" for i, f in enumerate(files))
    await update.message.reply_text(
        f"📋 Files queued ({len(files)}):\n{lines}\n\n"
        f"✏️ TYPE THE NAME FOR YOUR ZIP FILE:\n"
        f"(example: my-music)\n"
        f"I will add .zip automatically"
    )
    return NAMING


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    raw = update.message.text.strip()
    name = raw.replace(".zip", "").strip()
    name = "".join(c for c in name if c.isalnum() or c in "-_ ").strip()

    if not name:
        await update.message.reply_text("⚠️ Invalid name. Try again (letters, numbers, dashes only):")
        return NAMING

    files = user_files.get(uid, [])
    if not files:
        await update.message.reply_text("📭 No files! Send files first.")
        return COLLECTING

    await update.message.reply_text(f"⏳ Creating {name}.zip with {len(files)} file(s)...")

    zip_path = os.path.join(TMP_DIR, f"{name}_{uid}.zip")

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                if os.path.exists(f["path"]):
                    zf.write(f["path"], arcname=f["name"])

        size = os.path.getsize(zip_path)
        size_str = f"{size/1048576:.2f} MB" if size >= 1048576 else f"{size/1024:.1f} KB"

        with open(zip_path, "rb") as zf:
            await update.message.reply_document(
                document=zf,
                filename=f"{name}.zip",
                caption=f"✅ Done!\n📁 {name}.zip\n📦 {len(files)} files\n💾 {size_str}"
            )

        for f in files:
            try: os.remove(f["path"])
            except: pass
        try: os.remove(zip_path)
        except: pass
        user_files[uid] = []

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

    return COLLECTING


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(
                filters.Document.ALL | filters.PHOTO | filters.VIDEO |
                filters.AUDIO | filters.VOICE,
                receive_file
            ),
        ],
        states={
            COLLECTING: [
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO | filters.VIDEO |
                    filters.AUDIO | filters.VOICE,
                    receive_file
                ),
                CommandHandler("zip", zip_cmd),
                CommandHandler("list", list_cmd),
                CommandHandler("clear", clear_cmd),
            ],
            NAMING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name),
                CommandHandler("clear", clear_cmd),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("clear", clear_cmd),
        ],
        allow_reentry=True,
        name="zip_conversation",
        persistent=False,
    )

    app.add_handler(conv)
    logger.info("✅ ZIP Bot v3 running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
