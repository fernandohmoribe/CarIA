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

from database import SessionLocal, Usuario, criar_usuario_com_senha


def cmd_add(args) -> None:
    db = SessionLocal()
    try:
        usuario = criar_usuario_com_senha(db, args.nome_usuario, args.senha, args.nome)
        print(f"Usuário '{usuario.nome_usuario}' ({usuario.nome}) criado/senha atualizada com sucesso.")
    finally:
        db.close()


def cmd_list(args) -> None:
    db = SessionLocal()
    try:
        usuarios = db.query(Usuario).order_by(Usuario.nome_usuario.asc()).all()
        if not usuarios:
            print("Nenhum usuário cadastrado ainda.")
            return
        for usuario in usuarios:
            login = "tem login próprio" if usuario.senha_hash else "sem senha (só sistema, ex: IA)"
            print(f"  {usuario.nome_usuario:20s} | {usuario.nome or '—':25s} | {login}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Gerencia usuários/logins do painel admin")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Cria um usuário novo ou atualiza a senha de um existente")
    p_add.add_argument("nome_usuario")
    p_add.add_argument("senha")
    p_add.add_argument("--nome", default=None, help="Nome de exibição (padrão: o próprio nome_usuario)")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="Lista os usuários cadastrados")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
