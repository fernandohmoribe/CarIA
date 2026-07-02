#!/bin/bash
# Executa UMA VEZ no servidor para preparar o ambiente.
# Uso: bash setup_server.sh

set -e

echo "=== Atualizando sistema ==="
apt update && apt upgrade -y

echo "=== Instalando dependências ==="
apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git curl ufw

echo "=== Instalando Docker ==="
curl -fsSL https://get.docker.com | sh
apt install -y docker-compose-plugin

echo "=== Configurando firewall ==="
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "=== Criando diretório do projeto ==="
mkdir -p /opt/cariar
mkdir -p /opt/cariar/db

echo ""
echo "✅ Servidor pronto!"
echo ""
echo "Próximos passos:"
echo "  1. Copie os arquivos do projeto para /opt/cariar/"
echo "  2. Configure o .env"
echo "  3. Execute: bash deploy/start.sh"
