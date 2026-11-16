#!/bin/bash
# QUANT-LAB startup script: runs the trading bot and the dashboard server.
# Keys are read from a local .env file (never hard-coded). See .env.example.

DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$DIR/.env" ]; then
  echo "Missing $DIR/.env. Copy .env.example to .env and add your Alpaca paper keys."
  exit 1
fi

set -a
source "$DIR/.env"
set +a

pkill -f regime_bot.py 2>/dev/null
pkill -f server.py 2>/dev/null
sleep 2

echo "Starting trading bot..."
nohup python3 "$DIR/regime_bot.py" >> "$HOME/regime_bot.log" 2>&1 &
echo "  Bot PID: $!"

sleep 2

echo "Starting dashboard server..."
nohup python3 "$DIR/server.py" >> "$DIR/server.log" 2>&1 &
echo "  Dashboard PID: $!"

echo "Dashboard: http://localhost:5000"
echo "Bot log:   tail -f ~/regime_bot.log"
