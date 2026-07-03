import os

from fastapi import Request
from fastapi.responses import RedirectResponse

from database import SessionLocal, verify_user_credentials


def check_credentials(username: str, password: str) -> bool:
    """Aceita o login único do .env (bootstrap/compatibilidade — nunca quebra o setup
    existente) OU um login individual criado via manage_users.py (users.password_hash)."""
    if username == os.getenv("ADMIN_USERNAME", "admin") and password == os.getenv("ADMIN_PASSWORD", ""):
        return True

    db = SessionLocal()
    try:
        return verify_user_credentials(db, username, password)
    finally:
        db.close()


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("logged_in"))


def require_login(request: Request):
    """Se não estiver logado, retorna um RedirectResponse. Senão, retorna None."""
    if not is_logged_in(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return None
