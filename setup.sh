#!/usr/bin/env bash
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== CarIA — Setup ===${NC}\n"

# 1. .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${YELLOW}[1/3] Arquivo .env criado. Edite-o com suas chaves antes de continuar.${NC}"
    echo "      nano .env"
    exit 1
else
    echo "[1/3] .env já existe ✓"
fi

# 2. Python deps
echo "[2/3] Instalando dependências Python..."
pip install -r requirements.txt -q

# 3. Docker (WAHA)
echo "[3/3] Subindo WAHA..."
docker compose up -d
echo "      Aguardando WAHA iniciar..."
sleep 15

echo ""
echo -e "${GREEN}=== Pronto! Próximos passos ===${NC}"
echo ""
echo "  1. Acesse o dashboard WAHA em http://localhost:8080/dashboard"
echo "     e conecte o WhatsApp escaneando o QR Code."
echo ""
echo "  2. Sincronize o estoque de veículos:"
echo "     python sync_inventory.py"
echo ""
echo "  3. Inicie o backend Python:"
echo "     python main.py"
echo ""
echo "  4. Envie uma mensagem para o número conectado e teste!"
echo ""
echo "  Painel administrativo: http://localhost:3000/admin/login"
echo "  Health check:          http://localhost:3000/health"
