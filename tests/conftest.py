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

# Scripts manuais que batem na IA de verdade (chat_manual.py, scenarios_manual.py,
# eval_prompt.py) não usam prefixo test_ de propósito — ver CLAUDE.md — então o pytest nem
# tenta coletar nada deles, sem precisar de collect_ignore aqui.
