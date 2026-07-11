# CarIA — CLAUDE.md

Atendente de veículos via WhatsApp (WAHA + Claude) para revendas — piloto atual: Garcia Multimarcas.

## Convenções de teste

**Toda implementação nova precisa de teste automatizado** (unitário e/ou integração, via `pytest`).

### Duas categorias de teste, bem separadas

1. **Suíte automatizada (`pytest tests/`)** — `tests/test_*.py`. Roda livre, sem custo, sem depender
   de rede: todo teste que passa pelo fluxo de mensagem mocka `get_ai_response`
   (`unittest.mock.patch`), nunca bate na IA de verdade. Pode rodar quantas vezes quiser, sem pedir
   autorização.
   - Lógica de banco (`database.py`) e regras de negócio puras (ex: cálculo de prioridade,
     transição de status) merecem teste unitário direto — usar SQLite em memória
     (`sqlite:///:memory:`) ou um arquivo temporário por teste (ver `tests/conftest.py`).
   - Rotas do admin (`admin/routes.py`) e do webhook (`main.py`) merecem teste de integração com
     `fastapi.testclient.TestClient`.

2. **Scripts manuais que batem na IA de verdade** — `tests/chat_manual.py`,
   `tests/scenarios_manual.py`, `tests/eval_prompt.py`. Chamam a API Anthropic pra valer: custam
   dinheiro (tokens) e dependem de rede. De propósito **sem** prefixo `test_` no nome — o pytest
   nem tenta coletar. **Nunca rode esses scripts (nem `python tests/chat_manual.py` nem similares)
   sem pedir autorização explícita ao usuário antes** — é gasto real, não estimado. Uso:
   ```
   python tests/chat_manual.py            # chat interativo
   python tests/scenarios_manual.py       # cenários pré-definidos
   python tests/eval_prompt.py            # avaliação do system prompt
   ```

Comportamento da IA (prompt, tool use) só é validado de verdade pelos scripts manuais da categoria
2 — não dá pra automatizar isso de forma determinística e barata. Mas toda a lógica de **código**
ao redor da IA (dispatch de tools, persistência de lead, silenciar o bot, histórico de status,
etc) é testável e testada na categoria 1.

## Custo — a IA nunca recebe imagem

**A IA (chamadas pra `claude_agent.get_ai_response`) nunca recebe conteúdo de imagem — nem bloco
de imagem da API, nem base64, nem URL tratada como visão.** Imagem de veículo é só texto (nome do
arquivo, contagem, URL como string solta) — enviar imagem de verdade pro modelo custa muito mais
tokens (visão) do que texto. Isso vale pra qualquer feature nova que envolva foto:

- A tool `enviar_fotos_veiculo` retorna só `{"veiculo": ..., "fotos_enviadas": N}` pro Claude — os
  caminhos reais (`_fotos`) são removidos antes do `tool_result` ir pra API, e só a camada de
  envio (`main.py`/`admin/routes.py`) usa esse dado, pra mandar via WAHA ou mostrar na tela.
- Se algum dia quiser "a IA descrever uma foto" ou algo do tipo, isso É enviar imagem pro modelo —
  para e confirma com o usuário antes de implementar, não é uma decisão a tomar sozinho.
