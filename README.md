# 🗜️ ZIP Bot — Setup Guide

## Step 1 — Get a Telegram Bot Token
1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Give it a name (e.g. "My ZIP Bot") and username (e.g. "myzipbot")
4. Copy the token it gives you

## Step 2 — Add Your Token
Open `.env` and paste your token:
```
TELEGRAM_TOKEN=7123456789:AAFxxxxxxxxxxxxxxx
```

## Step 3 — Install Python
Download from **https://python.org** (version 3.10 or higher)
✅ Check "Add Python to PATH" during install

## Step 4 — Install Dependencies
Open terminal in this folder and run:
```bash
pip install -r requirements.txt
```

## Step 5 — Run the Bot
```bash
python bot.py
```

You'll see: `✅ ZIP Bot is running...`

## Step 6 — Use It on Telegram
1. Open Telegram → find your bot
2. Send `/start`
3. Send any files (documents, photos, videos, audio)
4. Send `/zip` → receive your compressed ZIP file!

---

## Commands
| Command | Action |
|---------|--------|
| `/start` | Welcome message |
| `/zip` | Compress all files & send ZIP |
| `/list` | See queued files |
| `/clear` | Remove all queued files |
| `/help` | Show help |

## Supported File Types
✅ Documents (PDF, DOCX, XLSX, etc.)
✅ Photos
✅ Videos
✅ Audio files
✅ Voice messages

## Notes
- Telegram has a **50MB upload limit** per file
- Files are deleted from the server after zipping
- Each user's files are kept separate
