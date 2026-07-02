import os

from fastapi import Request
from fastapi.responses import RedirectResponse


def check_credentials(username: str, password: str) -> bool:
    return (
        username == os.getenv("ADMIN_USERNAME", "admin")
        and password == os.getenv("ADMIN_PASSWORD", "")
    )


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("logged_in"))


def require_login(request: Request):
    """Se não estiver logado, retorna um RedirectResponse. Senão, retorna None."""
    if not is_logged_in(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return None
