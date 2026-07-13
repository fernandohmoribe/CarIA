import os

from fastapi import Request
from fastapi.responses import RedirectResponse

from database import SessionLocal, verificar_credenciais_usuario


def verificar_credenciais(nome_usuario: str, senha: str) -> bool:
    """Aceita o login único do .env (bootstrap/compatibilidade — nunca quebra o setup
    existente) OU um login individual criado via manage_users.py (usuarios.senha_hash)."""
    if nome_usuario == os.getenv("ADMIN_USERNAME", "admin") and senha == os.getenv("ADMIN_PASSWORD", ""):
        return True

    db = SessionLocal()
    try:
        return verificar_credenciais_usuario(db, nome_usuario, senha)
    finally:
        db.close()


def esta_logado(request: Request) -> bool:
    return bool(request.session.get("logado"))


def exigir_login(request: Request):
    """Se não estiver logado, retorna um RedirectResponse. Senão, retorna None."""
    if not esta_logado(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return None
