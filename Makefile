PYTHON ?= python
VENV ?= .venv
PORT ?= 3000
USER ?=
PASSWORD ?=
NAME ?=
CONFIRM_AI_COST ?= 0

ifeq ($(OS),Windows_NT)
VENV_PYTHON := $(VENV)/Scripts/python.exe
else
VENV_PYTHON := $(VENV)/bin/python
endif

.DEFAULT_GOAL := help

.PHONY: help setup env dirs venv install run serve sync test check \
	waha-up waha-down waha-restart waha-logs user-add user-list \
	chat-manual scenarios-manual eval-prompt

help: ## Mostra os comandos disponíveis
	@echo "CarIA - comandos disponíveis"
	@echo ""
	@echo "  make setup             Prepara .env, diretórios, virtualenv e dependências"
	@echo "  make install           Instala as dependências Python"
	@echo "  make run               Inicia o backend com reload (desenvolvimento)"
	@echo "  make serve             Inicia o backend sem reload"
	@echo "  make sync              Sincroniza veículos e fotos do Supabase"
	@echo "  make test              Executa a suíte automatizada (sem IA real)"
	@echo "  make check             Valida sintaxe e executa os testes"
	@echo "  make waha-up           Sobe o WAHA via Docker Compose"
	@echo "  make waha-down         Para o WAHA"
	@echo "  make waha-restart      Reinicia o WAHA"
	@echo "  make waha-logs         Acompanha os logs do WAHA"
	@echo "  make user-list         Lista usuários do painel"
	@echo "  make user-add USER=u PASSWORD=p [NAME='Nome']"
	@echo "  make chat-manual CONFIRM_AI_COST=1       Chat com Anthropic real"
	@echo "  make scenarios-manual CONFIRM_AI_COST=1  Cenários com Anthropic real"
	@echo "  make eval-prompt CONFIRM_AI_COST=1        Avaliação com Anthropic real"

setup: env dirs venv install ## Prepara o ambiente local
	@echo "Setup concluído. Revise o arquivo .env antes de iniciar os serviços."

env: ## Cria .env a partir do exemplo, sem sobrescrever configuração existente
	@$(PYTHON) -c "from pathlib import Path; p=Path('.env'); p.exists() or p.write_bytes(Path('.env.example').read_bytes())"

dirs: ## Cria diretórios locais de dados
	@$(PYTHON) -c "from pathlib import Path; [Path(p).mkdir(parents=True, exist_ok=True) for p in ('db', 'media')]"

venv: ## Cria o virtualenv Python
	@$(PYTHON) -m venv $(VENV)

install: venv ## Instala as dependências no virtualenv
	@$(VENV_PYTHON) -m pip install --upgrade pip
	@$(VENV_PYTHON) -m pip install -r requirements.txt

run: dirs ## Inicia em modo de desenvolvimento
	@$(VENV_PYTHON) main.py

serve: dirs ## Inicia Uvicorn sem reload
	@$(VENV_PYTHON) -m uvicorn main:app --host 0.0.0.0 --port $(PORT)

sync: dirs ## Sincroniza estoque e fotos do Supabase
	@$(VENV_PYTHON) sync_inventory.py

test: ## Executa apenas testes automatizados, sem chamadas reais à IA
	@$(VENV_PYTHON) -m pytest tests -q

check: ## Valida sintaxe e executa testes automatizados
	@$(VENV_PYTHON) -m compileall -q main.py claude_agent.py database.py dealership_config.py inventory.py rate_limit.py sync_inventory.py manage_users.py admin connectors
	@$(VENV_PYTHON) -m pytest tests -q

waha-up: ## Sobe o container WAHA
	@docker compose up -d

waha-down: ## Para o container WAHA
	@docker compose down

waha-restart: ## Reinicia o container WAHA
	@docker compose restart waha

waha-logs: ## Acompanha os logs do WAHA
	@docker compose logs -f waha

user-list: dirs ## Lista usuários do painel
	@$(VENV_PYTHON) manage_users.py list

user-add: dirs ## Cria/atualiza usuário; requer USER e PASSWORD
	@$(VENV_PYTHON) -c "import sys; sys.exit(0 if '$(USER)' else 'Informe USER. Ex.: make user-add USER=joao PASSWORD=senha')"
	@$(VENV_PYTHON) -c "import sys; sys.exit(0 if '$(PASSWORD)' else 'Informe PASSWORD. Ex.: make user-add USER=joao PASSWORD=senha')"
	@$(VENV_PYTHON) manage_users.py add "$(USER)" "$(PASSWORD)" $(if $(NAME),--nome "$(NAME)",)

chat-manual: ## Chat real; requer confirmação de custo
	@$(VENV_PYTHON) -c "import sys; sys.exit(0 if '$(CONFIRM_AI_COST)' == '1' else 'Este comando consome a API Anthropic. Reexecute com CONFIRM_AI_COST=1.')"
	@$(VENV_PYTHON) tests/chat_manual.py

scenarios-manual: ## Cenários reais; requer confirmação de custo
	@$(VENV_PYTHON) -c "import sys; sys.exit(0 if '$(CONFIRM_AI_COST)' == '1' else 'Este comando consome a API Anthropic. Reexecute com CONFIRM_AI_COST=1.')"
	@$(VENV_PYTHON) tests/scenarios_manual.py

eval-prompt: ## Avaliação real; requer confirmação de custo
	@$(VENV_PYTHON) -c "import sys; sys.exit(0 if '$(CONFIRM_AI_COST)' == '1' else 'Este comando consome a API Anthropic. Reexecute com CONFIRM_AI_COST=1.')"
	@$(VENV_PYTHON) tests/eval_prompt.py
