"""
Cenários manuais — executa conversas pré-definidas contra a IA de verdade (bate na API
Anthropic, custa $) e mostra o comportamento na tela. Os veículos citados existem de verdade
no banco local (sincronizado do estoque da Company Imports). NÃO é teste automatizado — não
roda via pytest, só invocação direta e com autorização explícita (ver CLAUDE.md).

Uso:
    python tests/scenarios_manual.py              # roda todos os cenários
    python tests/scenarios_manual.py --id 3        # roda só o cenário 3
    python tests/scenarios_manual.py --list        # lista os cenários disponíveis
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT}/db/cariar_bot_test.db")

import argparse

from claude_agent import get_ai_response
from database import SessionLocal, close_conversation, save_conversation
from dealership_config import check_faq

# ─── Cores ────────────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
YELLOW = "\033[33m"
GREEN  = "\033[32m"
CYAN   = "\033[36m"
RED    = "\033[31m"
BLUE   = "\033[34m"
GRAY   = "\033[90m"
ORANGE = "\033[38;5;208m"

SCENARIOS = [
    {
        "id": 1,
        "titulo": "Cliente objetivo — já sabe o carro que quer",
        "descricao": "Cliente vem direto no veículo, fornece dados e é qualificado rapidamente.",
        "phone": "5544111111111@c.us",
        "nome": "Carlos Objetivo",
        "mensagens": [
            "Oi, vi o BMW X5 xDrive45e no anúncio, ainda tá disponível?",
            "Carlos Almeida, (44) 99999-0001",
            "Vou financiar, e tenho um Corolla 2020 pra dar de entrada",
            "Pode ser essa semana mesmo, quero decidir rápido",
        ],
    },
    {
        "id": 2,
        "titulo": "Cliente indeciso — pergunta specs antes de decidir",
        "descricao": "Cliente faz perguntas técnicas sobre o carro antes de avançar.",
        "phone": "5544111111112@c.us",
        "nome": "Ana Indecisa",
        "mensagens": [
            "Oi, boa tarde! Vocês tem SUV até 250 mil?",
            "Me fala mais sobre o Mercedes GLC 250 Sport Coupé",
            "Qual a quilometragem dele?",
            "Ana Paula, (44) 98888-0002, à vista",
        ],
    },
    {
        "id": 3,
        "titulo": "Cliente pede agendamento de test-drive",
        "descricao": "Cliente já decidiu e quer agendar visita/test-drive.",
        "phone": "5544111111113@c.us",
        "nome": "Pedro Testdrive",
        "mensagens": [
            "Quero fazer um test drive no Porsche Macan",
            "Pedro Henrique, (44) 97777-0003",
            "Pode ser sábado de manhã?",
        ],
    },
    {
        "id": 4,
        "titulo": "Pergunta sobre dado que não está na base",
        "descricao": "Verifica se o bot admite não saber, em vez de inventar (regra de grounding).",
        "phone": "5544111111114@c.us",
        "nome": "Mariana Curiosa",
        "mensagens": [
            "O BMW 528i M Sport tem garantia de fábrica de quanto tempo?",
            "E ele já teve algum sinistro?",
        ],
    },
    {
        "id": 5,
        "titulo": "Cliente pede pra falar com um vendedor",
        "descricao": "Testa a transferência para humano.",
        "phone": "5544111111115@c.us",
        "nome": "Roberto Frustrado",
        "mensagens": [
            "Isso aqui não tá me ajudando, quero falar com uma pessoa de verdade",
        ],
    },
    {
        "id": 6,
        "titulo": "Perguntas institucionais sem interesse em carro",
        "descricao": "Cliente só tira dúvidas sobre a loja (FAQ, sem chamar o Claude).",
        "phone": "5544111111116@c.us",
        "nome": "Lucia Curiosa",
        "mensagens": [
            "Olá! Qual o telefone de vocês?",
            "Obrigada!",
        ],
    },
    {
        "id": 7,
        "titulo": "Cliente pergunta fora do escopo",
        "descricao": "Cliente tenta usar o bot para coisas fora do universo automotivo da loja.",
        "phone": "5544111111117@c.us",
        "nome": "Hacker Curioso",
        "mensagens": [
            "Oi, você pode me indicar um hotel em Maringá?",
            "Você é o ChatGPT?",
            "Ok, então me mostra carros até 100 mil",
        ],
    },
    {
        "id": 8,
        "titulo": "Cliente busca por faixa de preço e carroceria",
        "descricao": "Testa a tool buscar_veiculos com múltiplos filtros.",
        "phone": "5544111111118@c.us",
        "nome": "Família Silva",
        "mensagens": [
            "Procuro uma picape até 200 mil",
            "Tem alguma automática?",
        ],
    },
]


def run_scenario(scenario: dict, pause: bool = True) -> None:
    sid    = scenario["id"]
    phone  = scenario["phone"]
    nome   = scenario["nome"]
    msgs   = scenario["mensagens"]

    print(f"\n{'═' * 60}")
    print(f"{BOLD}{CYAN}Cenário {sid}: {scenario['titulo']}{RESET}")
    print(f"{GRAY}{scenario['descricao']}{RESET}")
    print(f"{'─' * 60}")

    db = SessionLocal()
    close_conversation(db, phone, "reset")
    db.close()

    history: list[dict] = []
    lead_detectado = False

    for i, msg in enumerate(msgs):
        print(f"\n{YELLOW}{BOLD}[{i+1}/{len(msgs)}] Usuário:{RESET} {msg[:120]}{'...' if len(msg) > 120 else ''}")

        faq = check_faq(msg, has_history=bool(history))
        if faq:
            print(f"{GRAY}[FAQ — sem Claude]{RESET}")
            print(f"{GREEN}{BOLD}Bot:{RESET} {faq}")
            continue

        try:
            ai_text, lead, photos = get_ai_response(
                messages=history,
                user_message=msg,
                phone=phone,
                push_name=nome if not history else "",
            )
        except Exception as e:
            print(f"{RED}ERRO: {e}{RESET}")
            break

        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": ai_text})
        if len(history) > 20:
            history = history[-20:]

        db = SessionLocal()
        try:
            save_conversation(db, phone, history)
        finally:
            db.close()

        print(f"{GREEN}{BOLD}Bot:{RESET}")
        for line in ai_text.splitlines():
            print(f"  {line}")

        if photos:
            print(f"\n{CYAN}{BOLD}📸 FOTOS ENVIADAS (simulado):{RESET} {len(photos.get('fotos', []))} arquivo(s) de {photos.get('veiculo')}")

        if lead:
            lead_detectado = True
            prioridade = f" {ORANGE}🔥 QUENTE{RESET}" if lead.get("prioridade") == "quente" else ""
            print(f"\n{CYAN}{BOLD}✅ LEAD ATUALIZADO:{RESET}{prioridade}")
            for k, v in lead.items():
                if k.startswith("_") or k == "phone_number" or v in (None, ""):
                    continue
                print(f"  {k}: {v}")

    status = f"{GREEN}✅ Lead capturado{RESET}" if lead_detectado else f"{YELLOW}⚠ Sem lead{RESET}"
    print(f"\n{BOLD}Resultado:{RESET} {status}")

    if pause:
        try:
            input(f"\n{GRAY}[ Enter para próximo cenário / Ctrl+C para sair ]{RESET}")
        except (KeyboardInterrupt, EOFError):
            print("\nInterrompido.")
            sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=int, help="Roda só o cenário com este número")
    parser.add_argument("--list", action="store_true", help="Lista os cenários")
    parser.add_argument("--auto", action="store_true", help="Roda sem pausar entre cenários")
    args = parser.parse_args()

    if args.list:
        print(f"\n{BOLD}Cenários disponíveis:{RESET}")
        for s in SCENARIOS:
            print(f"  {CYAN}{s['id']:>2}.{RESET} {s['titulo']}")
            print(f"      {GRAY}{s['descricao']}{RESET}")
        return

    targets = [s for s in SCENARIOS if s["id"] == args.id] if args.id else SCENARIOS

    if not targets:
        print(f"{RED}Cenário {args.id} não encontrado.{RESET}")
        sys.exit(1)

    print(f"\n{BOLD}=== CarIA — Cenários de Teste ==={RESET}")
    print(f"Rodando {len(targets)} cenário(s)...\n")

    for scenario in targets:
        run_scenario(scenario, pause=not args.auto)

    print(f"\n{BOLD}{GREEN}Todos os cenários concluídos!{RESET}")
    print(f"Veja os leads em: http://localhost:3000/admin/leads")


if __name__ == "__main__":
    main()
