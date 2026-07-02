"""
Chat de teste local — testa a IA sem WhatsApp, salva no banco.

Uso:
    python tests/test_chat.py
    python tests/test_chat.py --name "João Silva"   # simula push_name
    python tests/test_chat.py --phone 5544999999999  # simula número específico

Comandos durante o chat:
    /reset    — limpa o histórico (nova conversa)
    /history  — mostra o histórico atual
    /quit     — sai
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT}/db/cariar_bot_test.db")

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")

from claude_agent import get_ai_response
from database import SessionLocal, get_conversation, close_conversation, save_conversation
from dealership_config import DEALERSHIP_NAME, check_faq

YELLOW = "\033[33m"
GREEN = "\033[32m"
CYAN = "\033[36m"
RED = "\033[31m"
ORANGE = "\033[38;5;208m"
BOLD = "\033[1m"
RESET_COLOR = "\033[0m"


def print_bot(text: str) -> None:
    print(f"\n{GREEN}{BOLD}🤖 Bot:{RESET_COLOR}")
    for line in text.splitlines():
        print(f"   {line}")
    print()


def print_photos(photos: dict) -> None:
    fotos = photos.get("fotos", [])
    print(f"{CYAN}{BOLD}📸 FOTOS ENVIADAS (simulado — {len(fotos)} arquivo(s) de {photos.get('veiculo')}):{RESET_COLOR}")
    for foto in fotos:
        origem = foto.get("local_path") or foto.get("url")
        print(f"   {origem}")
    print()


def print_lead(lead: dict) -> None:
    prioridade = f" {ORANGE}🔥 QUENTE{RESET_COLOR}" if lead.get("prioridade") == "quente" else ""
    print(f"{CYAN}{BOLD}📋 LEAD ATUALIZADO (notificaria o vendedor):{RESET_COLOR}{prioridade}")
    for k, v in lead.items():
        if k.startswith("_") or k == "phone_number" or v in (None, ""):
            continue
        print(f"   {k}: {v}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat de teste local")
    parser.add_argument("--name", default="Teste", help="Nome do cliente simulado")
    parser.add_argument("--phone", default="5500000000000", help="Número simulado")
    args = parser.parse_args()

    phone = f"{args.phone}@c.us"
    push_name = args.name

    db = SessionLocal()
    history = get_conversation(db, phone)
    db.close()

    print(f"\n{BOLD}=== {DEALERSHIP_NAME} — Chat de Teste ==={RESET_COLOR}")
    print(f"Cliente: {CYAN}{push_name}{RESET_COLOR}  |  Fone: {CYAN}{args.phone}{RESET_COLOR}")
    if history:
        print(f"Histórico existente: {len(history) // 2} trocas carregadas do banco")
    print(f"Comandos: {YELLOW}/reset{RESET_COLOR}  {YELLOW}/history{RESET_COLOR}  {YELLOW}/quit{RESET_COLOR}")
    print("─" * 40)

    while True:
        try:
            first_line = input(f"\n{YELLOW}{BOLD}Você:{RESET_COLOR} ")
        except (KeyboardInterrupt, EOFError):
            print("\nSaindo...")
            sys.exit(0)

        lines = [first_line]
        if first_line.strip():
            try:
                while True:
                    line = input(f"{YELLOW}     ...{RESET_COLOR} ")
                    if line == "":
                        break
                    lines.append(line)
            except (KeyboardInterrupt, EOFError):
                pass

        import re as _re
        user_input = _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", "\n".join(lines)).strip()

        if not user_input:
            continue

        if user_input == "/quit":
            sys.exit(0)

        if user_input == "/reset":
            db = SessionLocal()
            try:
                close_conversation(db, phone, "reset")
            finally:
                db.close()
            history.clear()
            print(f"{RED}Sessão encerrada (reset). Nova conversa iniciada.{RESET_COLOR}")
            continue

        if user_input == "/history":
            if not history:
                print("Histórico vazio.")
            for msg in history:
                role = "Você" if msg["role"] == "user" else "Bot"
                color = YELLOW if msg["role"] == "user" else GREEN
                print(f"  {color}[{role}]{RESET_COLOR} {msg['content'][:120]}")
            continue

        faq_answer = check_faq(user_input, has_history=bool(history))
        if faq_answer:
            print(f"\n{CYAN}[FAQ — sem chamar a IA]{RESET_COLOR}")
            print_bot(faq_answer)
            continue

        try:
            ai_text, lead_to_notify, photos_to_send = get_ai_response(
                messages=history,
                user_message=user_input,
                phone=phone,
                push_name=push_name if not history else "",
            )
        except Exception as e:
            print(f"{RED}Erro ao chamar a IA: {e}{RESET_COLOR}")
            continue

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": ai_text})
        if len(history) > 20:
            history = history[-20:]

        db = SessionLocal()
        try:
            save_conversation(db, phone, history)
        finally:
            db.close()

        print_bot(ai_text)

        if photos_to_send:
            print_photos(photos_to_send)

        if lead_to_notify:
            print_lead(lead_to_notify)


if __name__ == "__main__":
    main()
