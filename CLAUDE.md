# CarIA — CLAUDE.md

Atendente de veículos via WhatsApp (WAHA + Claude) para revendas — piloto atual: Company Imports.

## Convenções de teste

**Toda implementação nova precisa de teste automatizado** (unitário e/ou integração, via `pytest`) —
`tests/test_chat.py` e `tests/test_scenarios.py` são scripts manuais/exploratórios pra conversar com a
IA e ver o comportamento na tela, **não substituem teste automatizado com assert**.

- Testes ficam em `tests/`, arquivo `test_<módulo>.py`, rodam com `pytest`.
- Lógica de banco (`database.py`) e regras de negócio puras (ex: cálculo de prioridade, transição de
  status) merecem teste unitário direto, sem precisar do banco real — usar SQLite em memória
  (`sqlite:///:memory:`) ou um arquivo temporário por teste.
- Rotas do admin (`admin/routes.py`) e do webhook (`main.py`) merecem teste de integração com
  `fastapi.testclient.TestClient`.
- Comportamento da IA (prompt, tool use) continua validado manualmente via `tests/test_chat.py` /
  `tests/test_scenarios.py` — não dá pra automatizar de forma determinística, mas a lógica de
  código ao redor da IA (dispatch de tools, persistência de lead, silenciar o bot, etc) sim.
