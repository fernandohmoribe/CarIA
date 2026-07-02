import os
import subprocess
import sys
import tempfile
from pathlib import Path

from rate_limit import is_rate_limited

PROJECT_ROOT = Path(__file__).parent.parent


def test_rate_limit_allows_up_to_max_then_blocks():
    key = "unit-test-key-allow-then-block"
    for _ in range(3):
        assert is_rate_limited(key, max_requests=3, window_seconds=60, block_seconds=300) is False
    assert is_rate_limited(key, max_requests=3, window_seconds=60, block_seconds=300) is True


def test_rate_limit_stays_blocked_on_subsequent_calls_within_block_window():
    key = "unit-test-key-stays-blocked"
    for _ in range(2):
        is_rate_limited(key, max_requests=2, window_seconds=60, block_seconds=300)
    assert is_rate_limited(key, max_requests=2, window_seconds=60, block_seconds=300) is True
    # continua bloqueado mesmo em chamada seguinte, dentro da janela de bloqueio
    assert is_rate_limited(key, max_requests=2, window_seconds=60, block_seconds=300) is True


def test_rate_limit_keys_are_independent():
    assert is_rate_limited("unit-test-key-a", max_requests=1, window_seconds=60, block_seconds=300) is False
    assert is_rate_limited("unit-test-key-b", max_requests=1, window_seconds=60, block_seconds=300) is False


def test_admin_login_rate_limited_after_too_many_wrong_attempts():
    from fastapi.testclient import TestClient
    from main import app
    from admin.routes import LOGIN_RATE_LIMIT_MAX

    # username exclusivo desse teste — evita colidir com o contador dos outros testes que
    # também fazem login (todos usam "admin"), já que o TestClient sempre reporta o mesmo IP.
    username = "ratelimit_probe_user"

    client = TestClient(app)
    for _ in range(LOGIN_RATE_LIMIT_MAX):
        resp = client.post("/admin/login", data={"username": username, "password": "wrong"})
        assert resp.status_code == 401

    blocked = client.post("/admin/login", data={"username": username, "password": "wrong"})
    assert blocked.status_code == 429


def test_main_fails_fast_without_session_secret_key():
    """Sobe main.py num subprocesso limpo, sem SESSION_SECRET_KEY nem .env real acessível
    (cwd é um diretório temporário sem .env) — precisa falhar alto, não usar um valor padrão."""
    with tempfile.TemporaryDirectory() as tmp_cwd:
        env = os.environ.copy()
        env.pop("SESSION_SECRET_KEY", None)
        env["DATABASE_URL"] = f"sqlite:///{tmp_cwd}/probe.db"
        env["ANTHROPIC_API_KEY"] = "sk-ant-test-placeholder"
        env["ADMIN_USERNAME"] = "admin"
        env["ADMIN_PASSWORD"] = "test"
        env["TEST_PHONES"] = ""
        env["PYTHONPATH"] = str(PROJECT_ROOT)

        result = subprocess.run(
            [sys.executable, "-c", "import main"],
            cwd=tmp_cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

        assert result.returncode != 0
        assert "SESSION_SECRET_KEY" in result.stderr
