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
TMP_DIR = "/tmp"
MAX_TG_SIZE = 50 * 1024 * 1024  # 50MB Telegram limit

# Conversation states
COLLECTING = 1
NAMING = 2
UNZIPPING = 3

user_files = {}  # {uid: [{"name": ..., "path": ...}]}


def user_dir(uid):
    p = os.path.join(TMP_DIR, f"zb_{uid}")
    os.makedirs(p, exist_ok=True)
    return p


def human_size(b):
    if b >= 1_048_576:
        return f"{b/1_048_576:.2f} MB"
    return f"{b/1024:.1f} KB"


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *ZIP Bot v4*\n\n"
        "📦 *ZIP files:* Send files one by one, then /zip to compress\n"
        "📂 *UNZIP files:* Send a .zip file and I will extract everything\n\n"
        "*Commands:*\n"
        "/zip — Compress queued files into a ZIP\n"
        "/list — See queued files\n"
        "/clear — Clear all queued files\n"
        "/help — Show this message\n\n"
        "_Large ZIPs over 50MB are auto-split into parts!_",
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

    # Download the file
    path = os.path.join(user_dir(uid), name)
    try:
        fo = await context.bot.get_file(f.file_id)
        await fo.download_to_drive(path)
    except Exception as e:
        await msg.reply_text(f"❌ Download failed: {e}")
        return COLLECTING

    # If it's a ZIP — offer to unzip it
    if is_zip:
        context.user_data["unzip_path"] = path
        context.user_data["unzip_name"] = name
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📂 Unzip it", callback_data="do_unzip"),
                InlineKeyboardButton("📦 Add to queue", callback_data="add_to_queue"),
            ]
        ])
        await msg.reply_text(
            f"📥 Received *{name}*\n\nWhat do you want to do with it?",
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
        f"✅ *{name}* added!\n"
        f"📦 {n} file(s) queued.\n\n"
        f"Send more files or /zip to compress.",
        parse_mode="Markdown"
    )
    return COLLECTING


# ── Unzip callback ────────────────────────────────────────────────────────────

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "do_unzip":
        zip_path = context.user_data.get("unzip_path")
        zip_name = context.user_data.get("unzip_name", "archive.zip")

        if not zip_path or not os.path.exists(zip_path):
            await query.edit_message_text("❌ ZIP file not found. Please send it again.")
            return COLLECTING

        await query.edit_message_text(f"📂 Extracting *{zip_name}* ...", parse_mode="Markdown")

        extract_dir = os.path.join(TMP_DIR, f"unzip_{uid}")
        os.makedirs(extract_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                members = zf.namelist()
                zf.extractall(extract_dir)

            await query.message.reply_text(
                f"✅ Extracted *{len(members)}* file(s) from *{zip_name}*\n\n"
                f"Sending them now...",
                parse_mode="Markdown"
            )

            sent = 0
            skipped = 0
            for member in members:
                member_path = os.path.join(extract_dir, member)
                # Skip directories
                if os.path.isdir(member_path):
                    continue
                file_size = os.path.getsize(member_path)
                if file_size > MAX_TG_SIZE:
                    await query.message.reply_text(
                        f"⚠️ *{member}* is {human_size(file_size)} — too large for Telegram (50MB limit), skipping.",
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
                    await query.message.reply_text(f"⚠️ Could not send {member}: {e}")
                    skipped += 1

            summary = f"✅ *Done! Sent {sent} file(s)*"
            if skipped:
                summary += f"\n⚠️ Skipped {skipped} file(s) — too large or unreadable"
            await query.message.reply_text(summary, parse_mode="Markdown")

            # Cleanup
            shutil.rmtree(extract_dir, ignore_errors=True)
            try: os.remove(zip_path)
            except: pass

        except zipfile.BadZipFile:
            await query.message.reply_text("❌ That file is not a valid ZIP archive.")
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
            f"✅ *{zip_name}* added to queue!\n"
            f"📦 {n} file(s) queued.\n\n"
            f"Send more files or /zip to compress.",
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
        f"_Type the name and send it. No need to add .zip!_\n"
        f"_Example: my-music or project-backup_",
        parse_mode="Markdown"
    )
    return NAMING


# ── Receive name → compress & split if needed ─────────────────────────────────

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    raw = update.message.text.strip()
    name = raw.replace(".zip", "").strip()
    name = "".join(c for c in name if c.isalnum() or c in "-_ ").strip()

    if not name:
        await update.message.reply_text(
            "⚠️ Invalid name. Use letters, numbers, spaces or dashes.\nTry again:"
        )
        return NAMING

    files = user_files.get(uid, [])
    if not files:
        await update.message.reply_text("📭 No files to zip! Send files first.")
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

        # ── Split if over 50MB ────────────────────────────────────────────────
        if zip_size > MAX_TG_SIZE:
            await update.message.reply_text(
                f"📦 ZIP is *{human_size(zip_size)}* — too large for Telegram!\n"
                f"✂️ Splitting into parts...",
                parse_mode="Markdown"
            )

            part_size = MAX_TG_SIZE - (1 * 1024 * 1024)  # 49MB per part
            num_parts = math.ceil(zip_size / part_size)
            parts_sent = 0

            with open(zip_path, "rb") as zf:
                for i in range(num_parts):
                    part_data = zf.read(part_size)
                    if not part_data:
                        break
                    part_filename = f"{name}.zip.part{i+1:03d}"
                    part_path = os.path.join(TMP_DIR, f"{uid}_{part_filename}")
                    with open(part_path, "wb") as pf:
                        pf.write(part_data)

                    with open(part_path, "rb") as pf:
                        await update.message.reply_document(
                            document=pf,
                            filename=part_filename,
                            caption=(
                                f"📦 *{name}.zip* — Part {i+1} of {num_parts}\n"
                                f"💾 {human_size(len(part_data))}"
                            ),
                            parse_mode="Markdown"
                        )
                    parts_sent += 1
                    try: os.remove(part_path)
                    except: pass

            await update.message.reply_text(
                f"✅ *Done! Sent {parts_sent} parts.*\n\n"
                f"_To reassemble: rename parts to_ `{name}.zip.001`, `{name}.zip.002` _etc._\n"
                f"_Then use 7-Zip or WinRAR to join them._",
                parse_mode="Markdown"
            )

        else:
            # ── Send as single ZIP ────────────────────────────────────────────
            with open(zip_path, "rb") as zf:
                await update.message.reply_document(
                    document=zf,
                    filename=f"{name}.zip",
                    caption=(
                        f"✅ *Done! Here is your ZIP.*\n\n"
                        f"📁 *{name}.zip*\n"
                        f"📦 Files: *{len(files)}*\n"
                        f"💾 Size: *{human_size(zip_size)}*"
                    ),
                    parse_mode="Markdown"
                )

        # Cleanup
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
    logger.info("✅ ZIP Bot v4 running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
