"""
Configura um banco SQLite isolado (arquivo temporário) antes de qualquer módulo do
projeto ser importado — precisa rodar antes, porque database.py cria o engine no
import, a partir de DATABASE_URL.
"""

import os
import tempfile

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"
os.environ["TEST_PHONES"] = ""  # não deixa a whitelist do .env real (produção) vazar pro teste
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-placeholder")
os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret-key")
os.environ.setdefault("SEGUNDOS_MINIMOS_RESPOSTA", "0")  # sem isso, cada teste que chama
# processar_mensagem esperaria 5s de verdade — a lógica do atraso tem teste dedicado

# Scripts manuais que batem na IA de verdade (chat_manual.py, scenarios_manual.py,
# eval_prompt.py) não usam prefixo test_ de propósito — ver CLAUDE.md — então o pytest nem
# tenta coletar nada deles, sem precisar de collect_ignore aqui.

import pytest  # noqa: E402  (depois da configuração de ambiente acima, de propósito)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """rate_limit.py guarda estado em dicts a nível de módulo — sem isso, testes que fazem
    login repetidas vezes (ex: vários arquivos logando como "admin") acumulam tentativas ao
    longo da suíte inteira e acabam esbarrando no limite sem ter nada a ver uns com os outros,
    já que o TestClient sempre reporta o mesmo IP fake ("testclient")."""
    import rate_limit

    rate_limit._carimbos_tempo.clear()
    rate_limit._bloqueados.clear()
    yield
