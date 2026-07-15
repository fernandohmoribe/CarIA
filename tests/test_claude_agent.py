from unittest.mock import MagicMock, patch

from database import SessionLocal, obter_ou_criar_loja

import claude_agent


def _make_loja(db, nome="Loja Claude Agent"):
    return obter_ou_criar_loja(db, nome=nome, tipo_conector="supabase", config_conector={})


def _resposta_mock(texto: str, stop_reason: str = "end_turn"):
    bloco = MagicMock()
    bloco.type = "text"
    bloco.text = texto
    resposta = MagicMock()
    resposta.stop_reason = stop_reason
    resposta.content = [bloco]
    resposta.usage = MagicMock(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return resposta


def test_resposta_vazia_da_ia_cai_pra_mensagem_de_fallback():
    """Bug real visto em produção: a resposta final da IA saiu "" (texto vazio) — isso ficou
    salvo no histórico da conversa e quebrou TODAS as mensagens seguintes desse cliente, porque
    a Anthropic rejeita (400) qualquer conversa com bloco de texto vazio no histórico. Nunca pode
    devolver string vazia — precisa cair num fallback, tanto pro cliente quanto pro que é salvo."""
    db = SessionLocal()
    _make_loja(db)
    db.close()

    with patch.object(claude_agent._client.messages, "create", return_value=_resposta_mock("")):
        texto, lead, fotos = claude_agent.obter_resposta_ia(
            mensagens=[], mensagem_usuario="O que você tem de disponível?",
            telefone="5544900000201@c.us", nome_exibicao="Cliente Resposta Vazia",
        )

    assert texto == claude_agent.MENSAGEM_RESPOSTA_VAZIA
    assert texto != ""


def test_resposta_so_com_espacos_tambem_cai_pra_fallback():
    db = SessionLocal()
    _make_loja(db)
    db.close()

    with patch.object(claude_agent._client.messages, "create", return_value=_resposta_mock("   \n  ")):
        texto, lead, fotos = claude_agent.obter_resposta_ia(
            mensagens=[], mensagem_usuario="oi", telefone="5544900000202@c.us", nome_exibicao="Cliente Espacos",
        )

    assert texto == claude_agent.MENSAGEM_RESPOSTA_VAZIA


def test_resposta_normal_nao_e_afetada_pelo_fallback():
    db = SessionLocal()
    _make_loja(db)
    db.close()

    with patch.object(claude_agent._client.messages, "create", return_value=_resposta_mock("Olá! Como posso ajudar?")):
        texto, lead, fotos = claude_agent.obter_resposta_ia(
            mensagens=[], mensagem_usuario="oi", telefone="5544900000203@c.us", nome_exibicao="Cliente Normal",
        )

    assert texto == "Olá! Como posso ajudar?"
