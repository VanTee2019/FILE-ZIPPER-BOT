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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TMP_DIR = "/tmp"

# Conversation states
COLLECTING_FILES = 1
WAITING_FOR_NAME = 2

# Store files per user
user_files = {}


def get_user_dir(user_id):
    path = os.path.join(TMP_DIR, f"zipbot_{user_id}")
    os.makedirs(path, exist_ok=True)
    return path


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *ZIP Bot!*\n\n"
        "Send me files one by one.\n"
        "When you are done, send /zip and I will ask you what to name the ZIP file before compressing.\n\n"
        "*Commands:*\n"
        "/zip — Done sending files, compress now\n"
        "/list — See queued files\n"
        "/clear — Clear all files\n"
        "/help — Show this message",
        parse_mode="Markdown"
    )
    return COLLECTING_FILES


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)
    return COLLECTING_FILES


# ── Collect files ─────────────────────────────────────────────────────────────

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message

    tg_file = None
    file_name = None

    if message.document:
        tg_file = message.document
        file_name = tg_file.file_name or f"file_{tg_file.file_id[:8]}"
    elif message.photo:
        tg_file = message.photo[-1]
        file_name = f"photo_{tg_file.file_id[:8]}.jpg"
    elif message.video:
        tg_file = message.video
        file_name = tg_file.file_name or f"video_{tg_file.file_id[:8]}.mp4"
    elif message.audio:
        tg_file = message.audio
        file_name = tg_file.file_name or f"audio_{tg_file.file_id[:8]}.mp3"
    elif message.voice:
        tg_file = message.voice
        file_name = f"voice_{tg_file.file_id[:8]}.ogg"
    else:
        await message.reply_text("⚠️ Unsupported file type. Send documents, photos, videos or audio.")
        return COLLECTING_FILES

    # Download and save
    user_dir = get_user_dir(user_id)
    save_path = os.path.join(user_dir, file_name)

    try:
        file_obj = await context.bot.get_file(tg_file.file_id)
        await file_obj.download_to_drive(save_path)
    except Exception as e:
        await message.reply_text(f"❌ Failed to download file: {e}")
        return COLLECTING_FILES

    if user_id not in user_files:
        user_files[user_id] = []

    user_files[user_id].append({"name": file_name, "path": save_path})
    count = len(user_files[user_id])

    await message.reply_text(
        f"✅ *{file_name}* added!\n"
        f"📦 {count} file(s) queued.\n\n"
        f"Send more files or type /zip when done.",
        parse_mode="Markdown"
    )
    return COLLECTING_FILES


async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    files = user_files.get(user_id, [])

    if not files:
        await update.message.reply_text("📭 No files queued yet. Send me some files!")
        return COLLECTING_FILES

    file_list = "\n".join([f"  {i+1}. {f['name']}" for i, f in enumerate(files)])
    await update.message.reply_text(
        f"📋 *Queued files ({len(files)}):*\n\n{file_list}\n\n"
        f"Type /zip when you are done.",
        parse_mode="Markdown"
    )
    return COLLECTING_FILES


async def clear_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    for f in user_files.get(user_id, []):
        try:
            os.remove(f["path"])
        except Exception:
            pass
    user_files[user_id] = []
    await update.message.reply_text("🗑️ All files cleared! Send new files anytime.")
    return COLLECTING_FILES


# ── /zip — ask for name ───────────────────────────────────────────────────────

async def ask_zip_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    files = user_files.get(user_id, [])

    if not files:
        await update.message.reply_text(
            "📭 You have not sent any files yet!\n\nSend me some files first then type /zip."
        )
        return COLLECTING_FILES

    file_list = "\n".join([f"  {i+1}. {f['name']}" for i, f in enumerate(files)])
    await update.message.reply_text(
        f"📋 *Files ready to compress ({len(files)}):*\n\n{file_list}\n\n"
        f"📝 *What do you want to call the ZIP file?*\n"
        f"Just type the name and send it.\n"
        f"_Example: my-music or project-backup_\n"
        f"_(No need to add .zip — I will do that!)_",
        parse_mode="Markdown"
    )
    return WAITING_FOR_NAME


# ── Receive name and compress ─────────────────────────────────────────────────

async def receive_name_and_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Clean the name
    raw_name = update.message.text.strip()
    zip_name = raw_name.replace(".zip", "").strip()
    zip_name = "".join(c for c in zip_name if c.isalnum() or c in "-_ ()").strip()

    if not zip_name:
        await update.message.reply_text(
            "⚠️ That name is not valid.\n"
            "Please use letters, numbers, spaces or dashes.\n\n"
            "Try again — what should the ZIP be called?"
        )
        return WAITING_FOR_NAME

    files = user_files.get(user_id, [])
    if not files:
        await update.message.reply_text("📭 No files to zip! Send files first.")
        return COLLECTING_FILES

    await update.message.reply_text(
        f"⏳ Compressing *{len(files)}* file(s) into *{zip_name}.zip* ...",
        parse_mode="Markdown"
    )

    zip_path = os.path.join(TMP_DIR, f"{zip_name}_{user_id}.zip")

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                if os.path.exists(f["path"]):
                    zf.write(f["path"], arcname=f["name"])

        zip_size = os.path.getsize(zip_path)
        zip_size_str = (
            f"{zip_size / 1_048_576:.2f} MB" if zip_size >= 1_048_576
            else f"{zip_size / 1024:.1f} KB"
        )

        with open(zip_path, "rb") as zf:
            await update.message.reply_document(
                document=zf,
                filename=f"{zip_name}.zip",
                caption=(
                    f"✅ *Done! Here is your ZIP file.*\n\n"
                    f"📁 Name: *{zip_name}.zip*\n"
                    f"📦 Files inside: *{len(files)}*\n"
                    f"💾 Size: *{zip_size_str}*\n\n"
                    f"_Queue cleared. Send new files anytime!_"
                ),
                parse_mode="Markdown"
            )

        # Cleanup
        for f in files:
            try:
                os.remove(f["path"])
            except Exception:
                pass
        try:
            os.remove(zip_path)
        except Exception:
            pass
        user_files[user_id] = []

    except Exception as e:
        logger.error(f"ZIP error: {e}")
        await update.message.reply_text(
            f"❌ ZIP failed: {e}\n\nTry /clear and resend your files."
        )

    return COLLECTING_FILES


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(
                filters.Document.ALL | filters.PHOTO | filters.VIDEO |
                filters.AUDIO | filters.VOICE,
                receive_file
            ),
        ],
        states={
            COLLECTING_FILES: [
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO | filters.VIDEO |
                    filters.AUDIO | filters.VOICE,
                    receive_file
                ),
                CommandHandler("zip", ask_zip_name),
                CommandHandler("list", list_files),
                CommandHandler("clear", clear_files),
                CommandHandler("help", help_command),
            ],
            WAITING_FOR_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name_and_zip),
                CommandHandler("clear", clear_files),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("clear", clear_files),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)

    logger.info("✅ ZIP Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
