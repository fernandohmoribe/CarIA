"""
Gerencia logins do painel admin — cada pessoa (vendedor, administrador) tem usuário e senha
próprios, guardados com hash no banco (sem diferença de permissão entre eles ainda, ver
MELHORIAS). O login único do .env (ADMIN_USERNAME/ADMIN_PASSWORD) continua funcionando em
paralelo — isso aqui é só pra criar logins adicionais.

Uso:
    python manage_users.py add joao "senha-forte-aqui" --nome "João Vendedor"
    python manage_users.py list
"""

import argparse

from dotenv import load_dotenv

load_dotenv()

from database import SessionLocal, User, create_user_with_password


def cmd_add(args) -> None:
    db = SessionLocal()
    try:
        user = create_user_with_password(db, args.username, args.password, args.nome)
        print(f"Usuário '{user.username}' ({user.nome}) criado/senha atualizada com sucesso.")
    finally:
        db.close()


def cmd_list(args) -> None:
    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.username.asc()).all()
        if not users:
            print("Nenhum usuário cadastrado ainda.")
            return
        for user in users:
            login = "tem login próprio" if user.password_hash else "sem senha (só sistema, ex: IA)"
            print(f"  {user.username:20s} | {user.nome or '—':25s} | {login}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Gerencia usuários/logins do painel admin")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Cria um usuário novo ou atualiza a senha de um existente")
    p_add.add_argument("username")
    p_add.add_argument("password")
    p_add.add_argument("--nome", default=None, help="Nome de exibição (padrão: o próprio username)")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="Lista os usuários cadastrados")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
