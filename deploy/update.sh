#!/bin/bash
# Atualiza o bot após mudanças no código.
# Uso: bash deploy/update.sh

set -e
cd /opt/cariar

echo "=== Atualizando dependências ==="
source .venv/bin/activate
pip install -q -r requirements.txt

echo "=== Sincronizando estoque ==="
python sync_inventory.py

echo "=== Reiniciando bot ==="
systemctl restart cariar-bot
systemctl status cariar-bot --no-pager

echo ""
echo "✅ Atualizado!"
