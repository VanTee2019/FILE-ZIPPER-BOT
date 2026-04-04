#!/bin/bash
# This script starts the Telegram Local Bot API Server
# It must run BEFORE bot.py

# Install telegram-bot-api if not installed
if ! command -v telegram-bot-api &> /dev/null; then
    echo "Installing telegram-bot-api..."
    apt-get install -y telegram-bot-api 2>/dev/null || \
    snap install telegram-bot-api 2>/dev/null || \
    echo "Please install telegram-bot-api manually from https://github.com/tdlib/telegram-bot-api"
fi

# Load env variables
export $(grep -v '^#' .env | xargs)

# Start the local server
telegram-bot-api \
    --api-id=$API_ID \
    --api-hash=$API_HASH \
    --local \
    --dir=/tmp/telegram-bot-api \
    --port=8081 &

echo "✅ Local Bot API Server started on port 8081"
echo "🚀 Starting bot..."
sleep 2
python3 bot.py
