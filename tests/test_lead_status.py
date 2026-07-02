from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from database import (
    CLOSED_LEAD_STATUSES,
    Lead,
    MANUAL_LEAD_STATUSES,
    SILENCED_LEAD_STATUSES,
    SessionLocal,
    create_lead_after_closure,
    get_conversation,
    get_conversation_history_for_lead,
    get_latest_lead_status,
    get_or_create_dealership,
    save_conversation,
    set_lead_status,
)


def _make_dealership(db, nome="Loja Teste"):
    return get_or_create_dealership(db, nome=nome, connector_type="supabase", connector_config={})


def test_manual_and_closed_status_lists_are_consistent():
    # contatado/convertido/perdido precisam estar nos dois — são editáveis manualmente E fecham o lead
    assert CLOSED_LEAD_STATUSES == {"contatado", "convertido", "perdido"}
    assert CLOSED_LEAD_STATUSES.issubset(set(MANUAL_LEAD_STATUSES))
    assert "novo" not in MANUAL_LEAD_STATUSES
    assert "qualificado" not in MANUAL_LEAD_STATUSES
    assert "agendado" not in CLOSED_LEAD_STATUSES  # cliente pode ter dúvida antes da visita


def test_silenced_includes_transferido_but_transferido_is_not_closed():
    # transferido silencia o bot (IA já desistiu), mas não é "fechado": não reseta conversa
    # nem cria lead novo — é o mesmo atendimento, só esperando um humano assumir.
    assert SILENCED_LEAD_STATUSES == {"transferido", "contatado", "convertido", "perdido"}
    assert "transferido" in SILENCED_LEAD_STATUSES
    assert "transferido" not in CLOSED_LEAD_STATUSES


def test_set_lead_status_closed_resets_active_conversation():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Fechado")
    phone = "5544900000101@c.us"
    lead = Lead(dealership_id=dealership.id, phone_number=phone, status="agendado")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    save_conversation(db, phone, [{"role": "user", "content": "oi"}])
    assert get_conversation(db, phone) != []

    set_lead_status(db, lead, "convertido")

    assert lead.status == "convertido"
    assert get_conversation(db, phone) == []  # sessão foi resetada
    db.close()


def test_set_lead_status_non_closed_keeps_conversation():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Nao Fechado")
    phone = "5544900000102@c.us"
    lead = Lead(dealership_id=dealership.id, phone_number=phone, status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    save_conversation(db, phone, [{"role": "user", "content": "oi"}])
    set_lead_status(db, lead, "agendado")

    assert lead.status == "agendado"
    assert get_conversation(db, phone) != []
    db.close()


def test_set_lead_status_transferido_silences_without_resetting_conversation():
    # transferido silencia o bot (SILENCED_LEAD_STATUSES), mas não é CLOSED — a conversa
    # continua intacta, é o mesmo atendimento esperando um humano assumir.
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Transferido")
    phone = "5544900000108@c.us"
    lead = Lead(dealership_id=dealership.id, phone_number=phone, status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    save_conversation(db, phone, [{"role": "user", "content": "quero falar com uma pessoa"}])
    set_lead_status(db, lead, "transferido")

    assert lead.status == "transferido"
    assert get_conversation(db, phone) != []  # conversa NÃO foi resetada
    db.close()


def test_get_latest_lead_status_prefers_most_recent():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Ordem")
    phone = "5544900000103@c.us"

    older = Lead(dealership_id=dealership.id, phone_number=phone, status="perdido")
    db.add(older)
    db.commit()
    db.refresh(older)
    older.created_at = datetime.utcnow() - timedelta(days=1)
    db.commit()

    newer = Lead(dealership_id=dealership.id, phone_number=phone, status="novo")
    db.add(newer)
    db.commit()

    assert get_latest_lead_status(db, dealership.id, phone) == "novo"
    db.close()


def test_get_latest_lead_status_none_when_no_lead():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Vazia")
    assert get_latest_lead_status(db, dealership.id, "5544900000199@c.us") is None
    db.close()


def test_create_lead_after_closure_creates_fresh_novo_lead():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Reengajamento")
    phone = "5544900000104@c.us"

    lead = create_lead_after_closure(db, dealership.id, phone, "convertido")

    assert lead.status == "novo"
    assert lead.dealership_id == dealership.id
    assert "convertido" in lead.observacoes
    db.close()


def test_admin_can_change_status_via_route():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    client.post("/admin/login", data={"username": "admin", "password": "test-password"})

    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Painel")
    lead = Lead(dealership_id=dealership.id, phone_number="5544900000105@c.us", status="novo")
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()

    resp = client.post(f"/admin/leads/{lead_id}/status", data={"status": "contatado"}, follow_redirects=False)
    assert resp.status_code == 302

    db = SessionLocal()
    updated = db.query(Lead).filter(Lead.id == lead_id).first()
    assert updated.status == "contatado"
    db.close()


def test_admin_rejects_status_outside_manual_list():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    client.post("/admin/login", data={"username": "admin", "password": "test-password"})

    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Rejeita")
    lead = Lead(dealership_id=dealership.id, phone_number="5544900000106@c.us", status="novo")
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()

    # "qualificado" não é status manual — não deve ser aceito vindo do painel
    client.post(f"/admin/leads/{lead_id}/status", data={"status": "qualificado"}, follow_redirects=False)

    db = SessionLocal()
    unchanged = db.query(Lead).filter(Lead.id == lead_id).first()
    assert unchanged.status == "novo"
    db.close()


def test_webhook_silences_bot_for_closed_lead_and_creates_followup():
    import time

    from fastapi.testclient import TestClient
    import main
    from database import get_default_dealership

    db = SessionLocal()
    # get_default_dealership() sempre pega a primeira loja do banco — usa a mesma que o
    # webhook de verdade vai resolver, em vez de criar uma loja isolada pro teste.
    dealership = get_default_dealership(db) or _make_dealership(db, "Loja Webhook")
    dealership_id = dealership.id
    phone_num = "5544900000107"
    phone = f"{phone_num}@c.us"
    lead = Lead(dealership_id=dealership_id, phone_number=phone, status="perdido")
    db.add(lead)
    db.commit()
    db.close()

    payload = {
        "event": "message",
        "payload": {
            "fromMe": False,
            "from": phone,
            "hasMedia": False,
            "body": "oi, ainda quero saber de outro carro",
            "_data": {"notifyName": "Cliente Antigo"},
        },
    }

    with patch.object(main, "get_ai_response") as mock_ai, \
         patch.object(main, "send_message", new=AsyncMock()) as mock_send, \
         patch.object(main, "set_typing", new=AsyncMock()):
        client = TestClient(main.app)
        resp = client.post("/webhook/whatsapp", json=payload)
        assert resp.status_code == 200

        # a resposta do webhook não espera o asyncio.create_task terminar (é fire-and-forget,
        # de propósito, pra não travar o WAHA) — dá um tempo pro background task rodar.
        time.sleep(0.3)

        mock_ai.assert_not_called()  # bot não deve processar a mensagem via IA

    db = SessionLocal()
    leads = db.query(Lead).filter(Lead.dealership_id == dealership_id, Lead.phone_number == phone).all()
    db.close()
    assert len(leads) == 2  # o antigo "perdido" + o novo criado pro atendente revisar
    assert any(l.status == "novo" for l in leads)


def test_webhook_silences_bot_for_transferido_without_courtesy_or_followup():
    import time

    from fastapi.testclient import TestClient
    import main
    from database import get_default_dealership

    db = SessionLocal()
    dealership = get_default_dealership(db) or _make_dealership(db, "Loja Webhook Transferido")
    dealership_id = dealership.id
    phone = "5544900000109@c.us"
    lead = Lead(dealership_id=dealership_id, phone_number=phone, status="transferido")
    db.add(lead)
    db.commit()
    db.close()

    payload = {
        "event": "message",
        "payload": {
            "fromMe": False,
            "from": phone,
            "hasMedia": False,
            "body": "alo? cade o vendedor",
            "_data": {"notifyName": "Cliente Impaciente"},
        },
    }

    with patch.object(main, "get_ai_response") as mock_ai, \
         patch.object(main, "send_message", new=AsyncMock()) as mock_send, \
         patch.object(main, "set_typing", new=AsyncMock()):
        client = TestClient(main.app)
        resp = client.post("/webhook/whatsapp", json=payload)
        assert resp.status_code == 200
        time.sleep(0.3)

        mock_ai.assert_not_called()  # bot não processa via IA
        mock_send.assert_not_called()  # e não manda nenhuma mensagem, nem cortesia

    db = SessionLocal()
    leads = db.query(Lead).filter(Lead.dealership_id == dealership_id, Lead.phone_number == phone).all()
    db.close()
    assert len(leads) == 1  # não cria lead novo — é o mesmo atendimento aguardando o vendedor


# um telefone fixo por status, pra não colidir entre execuções do parametrize no mesmo banco
_STATUS_TEST_PHONES = {
    "novo": "5544900000200",
    "qualificado": "5544900000201",
    "agendado": "5544900000202",
    "transferido": "5544900000203",
    "contatado": "5544900000204",
    "convertido": "5544900000205",
    "perdido": "5544900000206",
}


@pytest.mark.parametrize("status", list(_STATUS_TEST_PHONES.keys()))
def test_webhook_ai_called_only_for_non_silenced_statuses(status):
    """Cobertura explícita dos 7 status: confirma se o bot chama a IA (responde) ou não,
    pra cada um — não só os casos de silêncio, mas também os 3 que devem responder normal."""
    import time

    from fastapi.testclient import TestClient
    import main
    from database import get_default_dealership

    db = SessionLocal()
    dealership = get_default_dealership(db) or _make_dealership(db, "Loja Parametrizada")
    dealership_id = dealership.id
    phone = f"{_STATUS_TEST_PHONES[status]}@c.us"
    lead = Lead(dealership_id=dealership_id, phone_number=phone, status=status)
    db.add(lead)
    db.commit()
    db.close()

    payload = {
        "event": "message",
        "payload": {
            "fromMe": False,
            "from": phone,
            "hasMedia": False,
            "body": "quero saber sobre um carro",
            "_data": {"notifyName": "Cliente Parametrizado"},
        },
    }

    with patch.object(main, "get_ai_response") as mock_ai, \
         patch.object(main, "send_message", new=AsyncMock()), \
         patch.object(main, "set_typing", new=AsyncMock()):
        mock_ai.return_value = ("resposta de teste", None, None)
        client = TestClient(main.app)
        resp = client.post("/webhook/whatsapp", json=payload)
        assert resp.status_code == 200
        time.sleep(0.3)

        if status in SILENCED_LEAD_STATUSES:
            mock_ai.assert_not_called()
        else:
            mock_ai.assert_called_once()


def test_conversation_history_scoped_to_lead_not_mixed_across_reopened_leads():
    """Reproduz o cenário que motivou conversations.lead_id: mesmo telefone gera um lead
    fechado e depois um lead novo (reengajamento) — as conversas de cada um não podem se
    misturar quando alguém abre o lead no painel."""
    import time

    from fastapi.testclient import TestClient
    import main
    from database import get_default_dealership

    db = SessionLocal()
    dealership = get_default_dealership(db) or _make_dealership(db, "Loja Escopo Conversa")
    dealership_id = dealership.id
    phone = "5544900000110@c.us"
    old_lead = Lead(dealership_id=dealership_id, phone_number=phone, status="novo")
    db.add(old_lead)
    db.commit()
    db.refresh(old_lead)
    old_lead_id = old_lead.id
    db.close()

    payload = {
        "event": "message",
        "payload": {
            "fromMe": False,
            "from": phone,
            "hasMedia": False,
            "body": "quero saber sobre um carro",
            "_data": {"notifyName": "Cliente Escopo"},
        },
    }

    # 1) primeira mensagem, ainda no lead antigo
    with patch.object(main, "get_ai_response") as mock_ai, \
         patch.object(main, "send_message", new=AsyncMock()), \
         patch.object(main, "set_typing", new=AsyncMock()):
        mock_ai.return_value = ("resposta pro lead antigo", None, None)
        client = TestClient(main.app)
        client.post("/webhook/whatsapp", json=payload)
        time.sleep(0.3)

    # 2) vendedor fecha o lead antigo (reseta a sessão)
    db = SessionLocal()
    old_lead = db.query(Lead).filter(Lead.id == old_lead_id).first()
    set_lead_status(db, old_lead, "perdido")
    db.close()

    # 3) cliente escreve de novo -> silenciado, cria lead novo automaticamente (cortesia)
    with patch.object(main, "get_ai_response") as mock_ai2, \
         patch.object(main, "send_message", new=AsyncMock()), \
         patch.object(main, "set_typing", new=AsyncMock()):
        client = TestClient(main.app)
        client.post("/webhook/whatsapp", json=payload)
        time.sleep(0.3)
        mock_ai2.assert_not_called()

    db = SessionLocal()
    new_lead = (
        db.query(Lead)
        .filter(Lead.dealership_id == dealership_id, Lead.phone_number == phone, Lead.status == "novo")
        .first()
    )
    assert new_lead is not None
    new_lead_id = new_lead.id
    db.close()

    # 4) próxima mensagem já processa normal no lead novo
    with patch.object(main, "get_ai_response") as mock_ai3, \
         patch.object(main, "send_message", new=AsyncMock()), \
         patch.object(main, "set_typing", new=AsyncMock()):
        mock_ai3.return_value = ("resposta pro lead novo", None, None)
        client = TestClient(main.app)
        client.post("/webhook/whatsapp", json=payload)
        time.sleep(0.3)

    db = SessionLocal()
    old_history = get_conversation_history_for_lead(db, old_lead_id)
    new_history = get_conversation_history_for_lead(db, new_lead_id)
    db.close()

    assert old_lead_id != new_lead_id
    assert len(old_history) >= 1
    assert len(new_history) >= 1
    assert all("resposta pro lead novo" not in c.messages_json for c in old_history)
    assert all("resposta pro lead antigo" not in c.messages_json for c in new_history)


def test_stale_conversation_expires_and_creates_new_session_for_same_lead():
    """Cenário diferente do reengajamento pós-fechamento: um lead "agendado" (nunca fechado
    manualmente) fica sem interação por mais de 24h — ao voltar, o bot NÃO silencia (status não é
    terminal), mas a sessão antiga expira e uma conversa nova é criada, ligada ao MESMO lead
    (não cria lead novo, porque ninguém fechou esse lead de verdade)."""
    import time
    from datetime import datetime, timedelta

    from fastapi.testclient import TestClient
    import main
    from database import Conversation, get_conversation_history_for_lead, get_default_dealership

    db = SessionLocal()
    dealership = get_default_dealership(db) or _make_dealership(db, "Loja Expiracao")
    dealership_id = dealership.id
    phone = "5544900000111@c.us"
    lead = Lead(dealership_id=dealership_id, phone_number=phone, status="agendado")
    db.add(lead)
    db.commit()
    db.refresh(lead)
    lead_id = lead.id

    # simula uma sessão antiga (mês passado) já ligada a esse lead
    old_conv = Conversation(
        phone_number=phone,
        lead_id=lead_id,
        status="active",
        messages_json='[{"role": "user", "content": "quero agendar"}]',
        created_at=datetime.utcnow() - timedelta(days=30),
        updated_at=datetime.utcnow() - timedelta(days=30),
    )
    db.add(old_conv)
    db.commit()
    old_conv_id = old_conv.id
    db.close()

    payload = {
        "event": "message",
        "payload": {
            "fromMe": False,
            "from": phone,
            "hasMedia": False,
            "body": "oi, ainda quero saber sobre esse carro",
            "_data": {"notifyName": "Cliente Antigo Agendado"},
        },
    }

    with patch.object(main, "get_ai_response") as mock_ai, \
         patch.object(main, "send_message", new=AsyncMock()), \
         patch.object(main, "set_typing", new=AsyncMock()):
        mock_ai.return_value = ("resposta depois de um mês", None, None)
        client = TestClient(main.app)
        resp = client.post("/webhook/whatsapp", json=payload)
        assert resp.status_code == 200
        time.sleep(0.3)
        mock_ai.assert_called_once()  # não foi silenciado — "agendado" não é status fechado

    db = SessionLocal()
    old_conv = db.query(Conversation).filter(Conversation.id == old_conv_id).first()
    assert old_conv.status == "expired"  # sessão antiga foi marcada como expirada

    # não deve ter criado um lead novo — só um lead esperado (o mesmo de sempre)
    leads = db.query(Lead).filter(Lead.dealership_id == dealership_id, Lead.phone_number == phone).all()
    assert len(leads) == 1
    assert leads[0].id == lead_id

    historico_conversas = get_conversation_history_for_lead(db, lead_id)
    assert len(historico_conversas) == 2  # a antiga (expirada) + a nova sessão
    db.close()
