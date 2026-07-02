from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from database import (
    Lead,
    MANUAL_LEAD_STATUSES,
    SessionLocal,
    TERMINAL_LEAD_STATUSES,
    create_lead_after_closure,
    get_conversation,
    get_latest_lead_status,
    get_or_create_dealership,
    save_conversation,
    set_lead_status,
)


def _make_dealership(db, nome="Loja Teste"):
    return get_or_create_dealership(db, nome=nome, connector_type="supabase", connector_config={})


def test_manual_and_terminal_status_lists_are_consistent():
    # convertido/perdido precisam estar nos dois — são editáveis manualmente E encerram o lead
    assert TERMINAL_LEAD_STATUSES.issubset(set(MANUAL_LEAD_STATUSES))
    assert "novo" not in MANUAL_LEAD_STATUSES
    assert "qualificado" not in MANUAL_LEAD_STATUSES


def test_set_lead_status_terminal_closes_active_conversation():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Terminal")
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


def test_set_lead_status_non_terminal_keeps_conversation():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Nao Terminal")
    phone = "5544900000102@c.us"
    lead = Lead(dealership_id=dealership.id, phone_number=phone, status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    save_conversation(db, phone, [{"role": "user", "content": "oi"}])
    set_lead_status(db, lead, "contatado")

    assert lead.status == "contatado"
    assert get_conversation(db, phone) != []
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


def test_webhook_silences_bot_for_terminal_lead_and_creates_followup():
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
         patch.object(main, "send_message", new=AsyncMock()) as mock_send:
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
