from database import (
    IA_USERNAME,
    Lead,
    SessionLocal,
    obter_historico_lead,
    obter_usuario_ia,
    obter_ou_criar_loja,
    obter_ou_criar_usuario,
    registrar_mudanca_status,
    definir_status_lead,
    atualizar_lead,
)


def _make_loja(db, nome="Loja Historico"):
    return obter_ou_criar_loja(db, nome=nome, tipo_conector="supabase", config_conector={})


def test_get_or_create_user_is_idempotent():
    db = SessionLocal()
    first = obter_ou_criar_usuario(db, "vendedor_joao", "João")
    second = obter_ou_criar_usuario(db, "vendedor_joao", "Nome Diferente")
    assert first.id == second.id
    assert second.nome == "João"  # não sobrescreve num get-or-create já existente
    db.close()


def test_get_ia_user_returns_same_special_user():
    db = SessionLocal()
    ia1 = obter_usuario_ia(db)
    ia2 = obter_usuario_ia(db)
    assert ia1.id == ia2.id
    assert ia1.nome_usuario == IA_USERNAME
    db.close()


def test_log_status_change_noop_when_status_unchanged():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Log Noop")
    lead = Lead(loja_id=loja.id, numero_telefone="5544900000300@c.us", status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    registrar_mudanca_status(db, lead.id, None, "novo", "novo")

    assert obter_historico_lead(db, lead.id) == []
    db.close()


def test_log_status_change_creates_entry_when_status_changes():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Log Muda")
    lead = Lead(loja_id=loja.id, numero_telefone="5544900000301@c.us", status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    usuario = obter_ou_criar_usuario(db, "vendedor_teste")
    registrar_mudanca_status(db, lead.id, usuario.id, "novo", "agendado", "cliente confirmou por telefone")

    historico = obter_historico_lead(db, lead.id)
    assert len(historico) == 1
    assert historico[0].status_anterior == "novo"
    assert historico[0].status_novo == "agendado"
    assert historico[0].observacao == "cliente confirmou por telefone"
    assert historico[0].usuario_id == usuario.id
    db.close()


def test_update_lead_logs_history_attributed_to_ia_when_status_changes():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Update IA")
    lead = Lead(loja_id=loja.id, numero_telefone="5544900000302@c.us", status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    atualizar_lead(db, lead, {"status": "qualificado", "forma_pagamento": "à vista"})

    historico = obter_historico_lead(db, lead.id)
    assert len(historico) == 1
    assert historico[0].status_anterior == "novo"
    assert historico[0].status_novo == "qualificado"
    assert historico[0].usuario.nome_usuario == IA_USERNAME
    db.close()


def test_update_lead_does_not_log_when_status_field_absent():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Update Sem Status")
    lead = Lead(loja_id=loja.id, numero_telefone="5544900000303@c.us", status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # atualiza só o veículo de interesse, sem tocar em status
    atualizar_lead(db, lead, {"veiculo_interesse": "BMW X5"})

    assert obter_historico_lead(db, lead.id) == []
    db.close()


def test_set_lead_status_logs_history_with_given_user_and_observacao():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Set Status Log")
    lead = Lead(loja_id=loja.id, numero_telefone="5544900000304@c.us", status="agendado")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    usuario = obter_ou_criar_usuario(db, "vendedor_maria", "Maria")
    definir_status_lead(db, lead, "perdido", usuario_id=usuario.id, observacao="comprou em outro lugar")

    historico = obter_historico_lead(db, lead.id)
    assert len(historico) == 1
    assert historico[0].status_anterior == "agendado"
    assert historico[0].status_novo == "perdido"
    assert historico[0].observacao == "comprou em outro lugar"
    assert historico[0].usuario.nome_usuario == "vendedor_maria"
    db.close()


def test_get_lead_historico_orders_most_recent_first():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Ordem Historico")
    lead = Lead(loja_id=loja.id, numero_telefone="5544900000305@c.us", status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    definir_status_lead(db, lead, "agendado")
    definir_status_lead(db, lead, "contatado")

    historico = obter_historico_lead(db, lead.id)
    assert [h.status_novo for h in historico] == ["contatado", "agendado"]
    db.close()


def test_admin_status_change_records_who_and_observacao():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    client.post("/admin/login", data={"nome_usuario": "admin", "senha": "test-password"})

    db = SessionLocal()
    loja = _make_loja(db, "Loja Painel Historico")
    lead = Lead(loja_id=loja.id, numero_telefone="5544900000306@c.us", status="novo")
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
    historico = obter_historico_lead(db, lead_id)
    assert len(historico) == 1
    assert historico[0].observacao == "liguei e confirmei interesse"
    assert historico[0].usuario.nome_usuario == "admin"
    db.close()


def test_lead_detail_page_renders_historico_section():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    client.post("/admin/login", data={"nome_usuario": "admin", "senha": "test-password"})

    db = SessionLocal()
    loja = _make_loja(db, "Loja Render Historico")
    lead = Lead(loja_id=loja.id, numero_telefone="5544900000307@c.us", status="novo")
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()

    db = SessionLocal()
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    definir_status_lead(db, lead, "agendado", observacao="visita marcada")
    db.close()

    resp = client.get(f"/admin/leads/{lead_id}")
    assert resp.status_code == 200
    assert "Histórico de status" in resp.text
    assert "visita marcada" in resp.text
