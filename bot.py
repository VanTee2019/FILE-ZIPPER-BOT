import os
import zipfile
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
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

user_files = {}        # {user_id: [{"name": ..., "path": ...}]}
waiting_for_name = {}  # {user_id: True}


def get_user_dir(user_id):
    path = os.path.join(TMP_DIR, f"zipbot_{user_id}")
    os.makedirs(path, exist_ok=True)
    return path


# ── Commands ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *ZIP Bot!*\n\n"
        "📤 Send me any files and I'll compress them into a ZIP.\n"
        "After sending your files, I'll ask you what to name the ZIP.\n\n"
        "*Commands:*\n"
        "/zip — Compress all sent files\n"
        "/list — See queued files\n"
        "/clear — Remove all pending files\n"
        "/help — Show this message",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    files = user_files.get(user_id, [])
    if not files:
        await update.message.reply_text("📭 No files queued. Send me some files first!")
        return
    file_list = "\n".join([f"  {i+1}. {f['name']}" for i, f in enumerate(files)])
    await update.message.reply_text(
        f"📋 *Queued files ({len(files)}):*\n\n{file_list}\n\nSend /zip to compress!",
        parse_mode="Markdown"
    )


async def clear_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    for f in user_files.get(user_id, []):
        try:
            os.remove(f["path"])
        except Exception:
            pass
    user_files[user_id] = []
    waiting_for_name.pop(user_id, None)
    await update.message.reply_text("🗑️ All files cleared. Start fresh!")


# ── File Receiver ─────────────────────────────────────────────────────────────

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
        await message.reply_text("⚠️ Unsupported file type.")
        return

    # Save file to user's tmp folder
    user_dir = get_user_dir(user_id)
    save_path = os.path.join(user_dir, file_name)

    try:
        file_obj = await context.bot.get_file(tg_file.file_id)
        await file_obj.download_to_drive(save_path)
    except Exception as e:
        await message.reply_text(f"❌ Failed to download: {e}")
        return

    if user_id not in user_files:
        user_files[user_id] = []

    user_files[user_id].append({"name": file_name, "path": save_path})
    count = len(user_files[user_id])

    logger.info(f"User {user_id} queued {count} file(s)")

    # ✅ After every file, ask for zip name
    waiting_for_name[user_id] = True
    await message.reply_text(
        f"✅ *{file_name}* received! ({count} file(s) queued)\n\n"
        f"📝 *What do you want to name the ZIP file?*\n"
        f"_(Type the name and send, or send more files first then type the name)_\n"
        f"_No need to add .zip — I'll do that for you!_",
        parse_mode="Markdown"
    )


# ── Text Handler — captures ZIP name ─────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if waiting_for_name.get(user_id):
        zip_name = update.message.text.strip()
        zip_name = zip_name.replace(".zip", "").strip()
        zip_name = "".join(c for c in zip_name if c.isalnum() or c in "-_ ()").strip()

        if not zip_name:
            await update.message.reply_text(
                "⚠️ Invalid name. Use letters, numbers, spaces or dashes. Try again:"
            )
            return

        files = user_files.get(user_id, [])
        if not files:
            waiting_for_name.pop(user_id, None)
            await update.message.reply_text("📭 No files to zip. Send some files first!")
            return

        waiting_for_name.pop(user_id, None)
        await update.message.reply_text(
            f"⏳ Zipping *{len(files)}* file(s) as *{zip_name}.zip*...",
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
                        f"✅ *Your ZIP is ready!*\n\n"
                        f"📁 Name: *{zip_name}.zip*\n"
                        f"📦 Files: *{len(files)}*\n"
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

    else:
        await update.message.reply_text(
            "📤 Send me files to zip, then I'll ask for the ZIP name!\n"
            "Use /help to see all commands."
        )


# ── /zip command — manual trigger ────────────────────────────────────────────

async def create_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    files = user_files.get(user_id, [])

    if not files:
        await update.message.reply_text("📭 No files queued! Send some files first.")
        return

    waiting_for_name[user_id] = True
    await update.message.reply_text(
        f"📝 *What do you want to name the ZIP file?*\n\n"
        f"You have *{len(files)}* file(s) ready.\n"
        f"Just type the name and send it!\n"
        f"_Example: my-music or project-files_",
        parse_mode="Markdown"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("zip", create_zip))
    app.add_handler(CommandHandler("list", list_files))
    app.add_handler(CommandHandler("clear", clear_files))
    app.add_handler(MessageHandler(
        filters.Document.ALL | filters.PHOTO | filters.VIDEO |
        filters.AUDIO | filters.VOICE,
        receive_file
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("✅ ZIP Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
