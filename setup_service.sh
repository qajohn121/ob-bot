#!/bin/bash
# setup_service.sh — run this ON the VPS to install ob-bot as a systemd service
# The bot will then start automatically on boot and restart if it crashes.
# Usage: bash setup_service.sh

set -e
BOT_DIR="/home/ubuntu/ob-bot"
VENV="$BOT_DIR/venv"
SERVICE_FILE="/etc/systemd/system/ob-bot.service"

echo "=== Installing ob-bot as systemd service ==="

# Install required Python packages
source "$VENV/bin/activate"
pip install pytz "python-telegram-bot[job-queue]" --quiet && echo "Dependencies OK"

# Write service file
sudo tee "$SERVICE_FILE" > /dev/null << 'SERVICE'
[Unit]
Description=OB Bot — AI Options Scanner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/ob-bot
EnvironmentFile=/home/ubuntu/ob-bot/.env
ExecStart=/home/ubuntu/ob-bot/venv/bin/python bot.py
Restart=always
RestartSec=15
StandardOutput=append:/home/ubuntu/ob-bot/data/ob_bot.log
StandardError=append:/home/ubuntu/ob-bot/data/ob_bot.log
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
SERVICE

echo "Service file written to $SERVICE_FILE"

# Stop any running bot
pkill -f bot.py 2>/dev/null || true
sleep 2

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable ob-bot
sudo systemctl start ob-bot
sleep 3

echo ""
echo "=== Service status ==="
sudo systemctl status ob-bot --no-pager -l

echo ""
echo "✅ ob-bot service installed!"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status ob-bot    # check if running"
echo "  sudo systemctl restart ob-bot   # restart after changes"
echo "  sudo systemctl stop ob-bot      # stop bot"
echo "  sudo journalctl -u ob-bot -f    # live logs"
echo "  tail -f $BOT_DIR/data/ob_bot.log  # file logs"
