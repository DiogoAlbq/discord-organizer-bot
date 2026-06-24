#!/usr/bin/env bash
# Cria /etc/systemd/system/discord-organizer-bot.service
# Nao roda sudo dentro do script - deixa pro usuario executar:
# sudo bash install_service.sh

set -e

SERVICE_FILE="/etc/systemd/system/discord-organizer-bot.service"
ENV_FILE="/etc/discord-organizer-bot.env"
PROJECT_DIR="/home/orion/projects/discord-organizer-bot"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/organizer"
USER="orion"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Discord Organizer Bot
After=network-online.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
WorkingDirectory=$PROJECT_DIR
ExecStart=$VENV_PYTHON service
Restart=on-failure
RestartSec=10
User=$USER

[Install]
WantedBy=multi-user.target
EOF

echo "Service file criado em $SERVICE_FILE"
echo "Crie $ENV_FILE com DISCORD_BOT_TOKEN=seu_token"
echo "Depois rode: sudo systemctl daemon-reload && sudo systemctl enable --now discord-organizer-bot"