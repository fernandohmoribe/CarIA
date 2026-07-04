"""Regressão do bug relatado: a IA calculou 'quinta-feira que vem' errado (caiu numa sexta).
Em vez de deixar a IA fazer a conta, ela só informa o NOME do dia (dia_visita) e o código
resolve a data certa — essa aritmética é testável e determinística, sem precisar de chamada
real à IA."""
from datetime import datetime
from unittest.mock import patch

from claude_agent import _handle_lead_tool, _resolver_dia_visita
from database import Lead, SessionLocal, get_or_create_dealership


def _fake_now(dt):
    return patch("claude_agent.datetime", **{"now.return_value": dt})


def test_resolver_dia_visita_hoje():
    with _fake_now(datetime(2026, 7, 4, 10, 0)):  # sábado
        assert _resolver_dia_visita("hoje", None) == "sábado, 04/07/2026"


def test_resolver_dia_visita_amanha_com_periodo():
    with _fake_now(datetime(2026, 7, 4, 10, 0)):  # sábado
        assert _resolver_dia_visita("amanhã", "manhã") == "domingo, 05/07/2026 de manhã"


def test_resolver_dia_visita_proxima_quinta_a_partir_de_sabado():
    """Bug relatado: hoje sábado 04/07/2026, IA disse 'quinta-feira, 10/07' — errado. A
    quinta-feira certa é 09/07/2026."""
    with _fake_now(datetime(2026, 7, 4, 10, 0)):  # sábado
        assert _resolver_dia_visita("quinta-feira", "manhã") == "quinta-feira, 09/07/2026 de manhã"


def test_resolver_dia_visita_mesmo_dia_da_semana_pula_pra_proxima_semana():
    """Se hoje já é quinta-feira e a IA manda dia_visita='quinta-feira' (em vez de 'hoje'),
    entende como a quinta que vem, não hoje — 'hoje' é o valor certo pra esse caso."""
    with _fake_now(datetime(2026, 7, 9, 10, 0)):  # quinta-feira
        assert _resolver_dia_visita("quinta-feira", None) == "quinta-feira, 16/07/2026"


def test_resolver_dia_visita_none_quando_nao_reconhecido():
    with _fake_now(datetime(2026, 7, 4, 10, 0)):
        assert _resolver_dia_visita("depois das férias", None) is None


def test_resolver_dia_visita_none_quando_vazio():
    assert _resolver_dia_visita(None, None) is None
    assert _resolver_dia_visita("", None) is None


def test_handle_lead_tool_resolve_dia_visita_para_preferencia_contato():
    db = SessionLocal()
    dealership = get_or_create_dealership(
        db, nome="Loja Dia Visita", connector_type="supabase", connector_config={}
    )
    dealership_id = dealership.id
    db.close()

    with _fake_now(datetime(2026, 7, 4, 10, 0)):  # sábado
        result = _handle_lead_tool(
            {"nome": "Cliente Teste", "dia_visita": "quinta-feira", "periodo_visita": "manhã"},
            dealership_id,
            "5544900000401@c.us",
        )

    assert result["preferencia_contato"] == "quinta-feira, 09/07/2026 de manhã"
    # dia_visita/periodo_visita não são campos do Lead — não podem vazar pro resultado
    assert "dia_visita" not in result
    assert "periodo_visita" not in result

    db = SessionLocal()
    lead = db.query(Lead).filter(Lead.phone_number == "5544900000401@c.us").first()
    assert lead.preferencia_contato == "quinta-feira, 09/07/2026 de manhã"
    db.close()


def test_handle_lead_tool_mantem_preferencia_contato_livre_quando_sem_dia_visita():
    """Fallback: cliente deu uma data específica ('15 de agosto') que não é um dia da semana —
    a IA usa preferencia_contato como texto livre, sem dia_visita, e isso passa direto."""
    db = SessionLocal()
    dealership = get_or_create_dealership(
        db, nome="Loja Dia Visita", connector_type="supabase", connector_config={}
    )
    dealership_id = dealership.id
    db.close()

    result = _handle_lead_tool(
        {"nome": "Outro Cliente", "preferencia_contato": "dia 15 de agosto"},
        dealership_id,
        "5544900000402@c.us",
    )

    assert result["preferencia_contato"] == "dia 15 de agosto"
