import os
import zipfile
import math
import logging
import shutil
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

TMP_DIR = "/tmp"
MAX_TG_SIZE = 50 * 1024 * 1024    # 50MB send limit
MAX_DOWNLOAD = 2 * 1024 * 1024 * 1024  # 2GB with local server

# Local Bot API Server URL — removes the 20MB limit
LOCAL_SERVER_URL = "http://localhost:8081"

# Conversation states
COLLECTING = 1
NAMING = 2

user_files = {}


def user_dir(uid):
    p = os.path.join(TMP_DIR, f"zb_{uid}")
    os.makedirs(p, exist_ok=True)
    return p


def human_size(b):
    if b >= 1_073_741_824:
        return f"{b/1_073_741_824:.2f} GB"
    if b >= 1_048_576:
        return f"{b/1_048_576:.2f} MB"
    return f"{b/1024:.1f} KB"


def get_file_size(f):
    try:
        return f.file_size or 0
    except Exception:
        return 0


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *ZIP Bot v5 — Large File Edition*\n\n"
        "📦 *ZIP:* Send files → /zip → name it → get ZIP\n"
        "📂 *UNZIP:* Send a .zip → bot extracts & sends files back\n\n"
        "✅ Supports files up to *2GB* via Local Bot API\n\n"
        "*Commands:*\n"
        "/zip — Compress queued files\n"
        "/list — See queued files\n"
        "/clear — Clear queue\n"
        "/help — Show this message",
        parse_mode="Markdown"
    )
    return COLLECTING


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)
    return COLLECTING


# ── File receiver ─────────────────────────────────────────────────────────────

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message

    f = None
    name = None
    is_zip = False

    if msg.document:
        f = msg.document
        name = f.file_name or f"doc_{f.file_id[:6]}"
        if name.lower().endswith(".zip"):
            is_zip = True
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
        await msg.reply_text("⚠️ Unsupported file type.")
        return COLLECTING

    file_size = get_file_size(f)

    # Check against 2GB limit
    if file_size > MAX_DOWNLOAD:
        await msg.reply_text(
            f"❌ *{name}* is *{human_size(file_size)}* — exceeds 2GB limit.",
            parse_mode="Markdown"
        )
        return COLLECTING

    await msg.reply_text(f"⬇️ Downloading *{name}* ({human_size(file_size)}) ...", parse_mode="Markdown")

    path = os.path.join(user_dir(uid), name)
    try:
        fo = await context.bot.get_file(f.file_id)
        await fo.download_to_drive(path)
    except Exception as e:
        await msg.reply_text(f"❌ Download failed: {e}")
        return COLLECTING

    # ZIP file — offer to unzip
    if is_zip:
        context.user_data["unzip_path"] = path
        context.user_data["unzip_name"] = name
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📂 Unzip it", callback_data="do_unzip"),
            InlineKeyboardButton("📦 Add to queue", callback_data="add_to_queue"),
        ]])
        await msg.reply_text(
            f"✅ *{name}* downloaded ({human_size(file_size)})\n\nWhat do you want to do?",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        return COLLECTING

    # Regular file — add to queue
    if uid not in user_files:
        user_files[uid] = []
    user_files[uid].append({"name": name, "path": path})
    n = len(user_files[uid])

    await msg.reply_text(
        f"✅ *{name}* added! ({human_size(file_size)})\n"
        f"📦 {n} file(s) queued.\n\n"
        f"Send more files or /zip to compress.",
        parse_mode="Markdown"
    )
    return COLLECTING


# ── Button callbacks ──────────────────────────────────────────────────────────

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "do_unzip":
        zip_path = context.user_data.get("unzip_path")
        zip_name = context.user_data.get("unzip_name", "archive.zip")

        if not zip_path or not os.path.exists(zip_path):
            await query.edit_message_text("❌ ZIP not found. Please send it again.")
            return COLLECTING

        await query.edit_message_text(f"📂 Extracting *{zip_name}* ...", parse_mode="Markdown")

        extract_dir = os.path.join(TMP_DIR, f"unzip_{uid}")
        os.makedirs(extract_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                members = zf.namelist()
                zf.extractall(extract_dir)

            await query.message.reply_text(
                f"✅ Found *{len(members)}* item(s) — sending now...",
                parse_mode="Markdown"
            )

            sent = 0
            skipped = 0
            for member in members:
                member_path = os.path.join(extract_dir, member)
                if os.path.isdir(member_path):
                    continue
                file_size = os.path.getsize(member_path)
                if file_size > MAX_TG_SIZE:
                    await query.message.reply_text(
                        f"⚠️ *{os.path.basename(member)}* is {human_size(file_size)} — too large to send, skipping.",
                        parse_mode="Markdown"
                    )
                    skipped += 1
                    continue
                try:
                    with open(member_path, "rb") as mf:
                        await query.message.reply_document(
                            document=mf,
                            filename=os.path.basename(member),
                            caption=f"📄 {os.path.basename(member)} ({human_size(file_size)})"
                        )
                    sent += 1
                except Exception as e:
                    await query.message.reply_text(f"⚠️ Could not send {os.path.basename(member)}: {e}")
                    skipped += 1

            summary = f"✅ *Extraction complete! Sent {sent} file(s)*"
            if skipped:
                summary += f"\n⚠️ Skipped {skipped} file(s) — too large"
            await query.message.reply_text(summary, parse_mode="Markdown")

            shutil.rmtree(extract_dir, ignore_errors=True)
            try: os.remove(zip_path)
            except: pass

        except zipfile.BadZipFile:
            await query.message.reply_text("❌ Not a valid ZIP file.")
        except Exception as e:
            await query.message.reply_text(f"❌ Extraction failed: {e}")

    elif query.data == "add_to_queue":
        zip_path = context.user_data.get("unzip_path")
        zip_name = context.user_data.get("unzip_name", "archive.zip")
        if uid not in user_files:
            user_files[uid] = []
        user_files[uid].append({"name": zip_name, "path": zip_path})
        n = len(user_files[uid])
        await query.edit_message_text(
            f"✅ *{zip_name}* added to queue!\n📦 {n} file(s) queued.\n\nSend more or /zip to compress.",
            parse_mode="Markdown"
        )

    return COLLECTING


# ── /list & /clear ────────────────────────────────────────────────────────────

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    files = user_files.get(uid, [])
    if not files:
        await update.message.reply_text("📭 No files queued.")
        return COLLECTING
    lines = "\n".join(f"{i+1}. {f['name']}" for i, f in enumerate(files))
    await update.message.reply_text(f"📋 *Queued ({len(files)}):*\n\n{lines}", parse_mode="Markdown")
    return COLLECTING


async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    for f in user_files.get(uid, []):
        try: os.remove(f["path"])
        except: pass
    user_files[uid] = []
    await update.message.reply_text("🗑️ Queue cleared!")
    return COLLECTING


# ── /zip — ask for name ───────────────────────────────────────────────────────

async def zip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    files = user_files.get(uid, [])
    if not files:
        await update.message.reply_text("📭 No files yet! Send files first then /zip.")
        return COLLECTING

    lines = "\n".join(f"{i+1}. {f['name']}" for i, f in enumerate(files))
    await update.message.reply_text(
        f"📋 *Files ready ({len(files)}):*\n\n{lines}\n\n"
        f"✏️ *What do you want to name the ZIP?*\n"
        f"_Type the name and send it — no need to add .zip!_",
        parse_mode="Markdown"
    )
    return NAMING


# ── Receive name → compress & auto-split ─────────────────────────────────────

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    raw = update.message.text.strip()
    name = raw.replace(".zip", "").strip()
    name = "".join(c for c in name if c.isalnum() or c in "-_ ").strip()

    if not name:
        await update.message.reply_text("⚠️ Invalid name. Try again:")
        return NAMING

    files = user_files.get(uid, [])
    if not files:
        await update.message.reply_text("📭 No files to zip!")
        return COLLECTING

    await update.message.reply_text(
        f"⏳ Compressing *{len(files)}* file(s) into *{name}.zip* ...",
        parse_mode="Markdown"
    )

    zip_path = os.path.join(TMP_DIR, f"{name}_{uid}.zip")

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                if os.path.exists(f["path"]):
                    zf.write(f["path"], arcname=f["name"])

        zip_size = os.path.getsize(zip_path)

        if zip_size > MAX_TG_SIZE:
            part_size = MAX_TG_SIZE - (1 * 1024 * 1024)
            num_parts = math.ceil(zip_size / part_size)
            await update.message.reply_text(
                f"📦 ZIP is *{human_size(zip_size)}* — splitting into *{num_parts} parts* ...",
                parse_mode="Markdown"
            )
            parts_sent = 0
            with open(zip_path, "rb") as zf:
                for i in range(num_parts):
                    part_data = zf.read(part_size)
                    if not part_data:
                        break
                    part_filename = f"{name}.part{i+1:03d}.zip"
                    part_path = os.path.join(TMP_DIR, f"{uid}_{part_filename}")
                    with open(part_path, "wb") as pf:
                        pf.write(part_data)
                    with open(part_path, "rb") as pf:
                        await update.message.reply_document(
                            document=pf,
                            filename=part_filename,
                            caption=f"📦 *{name}.zip* — Part {i+1}/{num_parts} ({human_size(len(part_data))})",
                            parse_mode="Markdown"
                        )
                    parts_sent += 1
                    try: os.remove(part_path)
                    except: pass

            await update.message.reply_text(
                f"✅ *Done! Sent {parts_sent} parts.*\n_Use 7-Zip or WinRAR to join them._",
                parse_mode="Markdown"
            )
        else:
            with open(zip_path, "rb") as zf:
                await update.message.reply_document(
                    document=zf,
                    filename=f"{name}.zip",
                    caption=(
                        f"✅ *Your ZIP is ready!*\n\n"
                        f"📁 *{name}.zip*\n"
                        f"📦 Files: *{len(files)}*\n"
                        f"💾 Size: *{human_size(zip_size)}*"
                    ),
                    parse_mode="Markdown"
                )

        for f in files:
            try: os.remove(f["path"])
            except: pass
        try: os.remove(zip_path)
        except: pass
        user_files[uid] = []

    except Exception as e:
        logger.error(f"ZIP error: {e}")
        await update.message.reply_text(f"❌ Failed: {e}\n\nTry /clear and resend.")

    return COLLECTING


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Connect to Local Bot API Server to bypass 20MB limit
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .local_mode(True)
        .base_url(f"{LOCAL_SERVER_URL}/bot")
        .base_file_url(f"{LOCAL_SERVER_URL}/file/bot")
        .build()
    )

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
                CommandHandler("help", help_command),
                CallbackQueryHandler(handle_buttons),
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
        name="zip_conv",
        persistent=False,
    )

    app.add_handler(conv)
    logger.info("✅ ZIP Bot v5 running with Local API Server!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
