from database import (
    IA_USERNAME,
    Lead,
    SessionLocal,
    get_ia_user,
    get_lead_historico,
    get_or_create_dealership,
    get_or_create_user,
    log_status_change,
    set_lead_status,
    update_lead,
)


def _make_dealership(db, nome="Loja Historico"):
    return get_or_create_dealership(db, nome=nome, connector_type="supabase", connector_config={})


def test_get_or_create_user_is_idempotent():
    db = SessionLocal()
    first = get_or_create_user(db, "vendedor_joao", "João")
    second = get_or_create_user(db, "vendedor_joao", "Nome Diferente")
    assert first.id == second.id
    assert second.nome == "João"  # não sobrescreve num get-or-create já existente
    db.close()


def test_get_ia_user_returns_same_special_user():
    db = SessionLocal()
    ia1 = get_ia_user(db)
    ia2 = get_ia_user(db)
    assert ia1.id == ia2.id
    assert ia1.username == IA_USERNAME
    db.close()


def test_log_status_change_noop_when_status_unchanged():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Log Noop")
    lead = Lead(dealership_id=dealership.id, phone_number="5544900000300@c.us", status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    log_status_change(db, lead.id, None, "novo", "novo")

    assert get_lead_historico(db, lead.id) == []
    db.close()


def test_log_status_change_creates_entry_when_status_changes():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Log Muda")
    lead = Lead(dealership_id=dealership.id, phone_number="5544900000301@c.us", status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    user = get_or_create_user(db, "vendedor_teste")
    log_status_change(db, lead.id, user.id, "novo", "agendado", "cliente confirmou por telefone")

    historico = get_lead_historico(db, lead.id)
    assert len(historico) == 1
    assert historico[0].status_anterior == "novo"
    assert historico[0].status_novo == "agendado"
    assert historico[0].observacao == "cliente confirmou por telefone"
    assert historico[0].user_id == user.id
    db.close()


def test_update_lead_logs_history_attributed_to_ia_when_status_changes():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Update IA")
    lead = Lead(dealership_id=dealership.id, phone_number="5544900000302@c.us", status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    update_lead(db, lead, {"status": "qualificado", "forma_pagamento": "à vista"})

    historico = get_lead_historico(db, lead.id)
    assert len(historico) == 1
    assert historico[0].status_anterior == "novo"
    assert historico[0].status_novo == "qualificado"
    assert historico[0].user.username == IA_USERNAME
    db.close()


def test_update_lead_does_not_log_when_status_field_absent():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Update Sem Status")
    lead = Lead(dealership_id=dealership.id, phone_number="5544900000303@c.us", status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # atualiza só o veículo de interesse, sem tocar em status
    update_lead(db, lead, {"veiculo_interesse": "BMW X5"})

    assert get_lead_historico(db, lead.id) == []
    db.close()


def test_set_lead_status_logs_history_with_given_user_and_observacao():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Set Status Log")
    lead = Lead(dealership_id=dealership.id, phone_number="5544900000304@c.us", status="agendado")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    user = get_or_create_user(db, "vendedor_maria", "Maria")
    set_lead_status(db, lead, "perdido", user_id=user.id, observacao="comprou em outro lugar")

    historico = get_lead_historico(db, lead.id)
    assert len(historico) == 1
    assert historico[0].status_anterior == "agendado"
    assert historico[0].status_novo == "perdido"
    assert historico[0].observacao == "comprou em outro lugar"
    assert historico[0].user.username == "vendedor_maria"
    db.close()


def test_get_lead_historico_orders_most_recent_first():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Ordem Historico")
    lead = Lead(dealership_id=dealership.id, phone_number="5544900000305@c.us", status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    set_lead_status(db, lead, "agendado")
    set_lead_status(db, lead, "contatado")

    historico = get_lead_historico(db, lead.id)
    assert [h.status_novo for h in historico] == ["contatado", "agendado"]
    db.close()


def test_admin_status_change_records_who_and_observacao():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    client.post("/admin/login", data={"username": "admin", "password": "test-password"})

    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Painel Historico")
    lead = Lead(dealership_id=dealership.id, phone_number="5544900000306@c.us", status="novo")
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()

    client.post(
        f"/admin/leads/{lead_id}/status",
        data={"status": "contatado", "observacao": "liguei e confirmei interesse"},
        follow_redirects=False,
    )

    db = SessionLocal()
    historico = get_lead_historico(db, lead_id)
    assert len(historico) == 1
    assert historico[0].observacao == "liguei e confirmei interesse"
    assert historico[0].user.username == "admin"
    db.close()


def test_lead_detail_page_renders_historico_section():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    client.post("/admin/login", data={"username": "admin", "password": "test-password"})

    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Render Historico")
    lead = Lead(dealership_id=dealership.id, phone_number="5544900000307@c.us", status="novo")
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()

    db = SessionLocal()
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    set_lead_status(db, lead, "agendado", observacao="visita marcada")
    db.close()

    resp = client.get(f"/admin/leads/{lead_id}")
    assert resp.status_code == 200
    assert "Histórico de status" in resp.text
    assert "visita marcada" in resp.text
