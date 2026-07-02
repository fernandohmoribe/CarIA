#!/bin/bash
# Inicia todos os serviços no servidor.
# Uso: bash deploy/start.sh

set -e
cd /opt/cariar

echo "=== Criando ambiente virtual Python ==="
python3 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "=== Subindo WAHA ==="
docker compose up -d

echo "=== Instalando serviço systemd ==="
cp deploy/cariar-bot.service /etc/systemd/system/cariar-bot.service
systemctl daemon-reload
systemctl enable cariar-bot
systemctl restart cariar-bot

echo "=== Configurando Nginx ==="
cp deploy/nginx.conf /etc/nginx/sites-available/cariar-bot
ln -sf /etc/nginx/sites-available/cariar-bot /etc/nginx/sites-enabled/cariar-bot
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "✅ Serviços no ar!"
echo ""
systemctl status cariar-bot --no-pager
