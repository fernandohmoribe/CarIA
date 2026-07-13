from fastapi.testclient import TestClient

from database import Lead, STATUS_LEAD_MANUAIS, SessionLocal, obter_loja_padrao, obter_ou_criar_loja
from main import app


def _logged_in_client() -> TestClient:
    client = TestClient(app)
    client.post("/admin/login", data={"nome_usuario": "admin", "senha": "test-password"})
    return client


def _make_lead(loja_id, telefone, status, nome="Cliente Teste", veiculo_interesse=None):
    db = SessionLocal()
    lead = Lead(
        loja_id=loja_id, numero_telefone=telefone, nome=nome, status=status,
        veiculo_interesse=veiculo_interesse,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    lead_id = lead.id
    db.close()
    return lead_id


def _loja_id():
    # leads_pagina (rota /admin/leads) sempre escopa por obter_loja_padrao() — os leads do
    # teste precisam pertencer a ESSA revenda, não a uma nova, senão não aparecem na página
    # (o banco de teste é compartilhado entre os arquivos da suíte, ver tests/conftest.py).
    db = SessionLocal()
    loja = obter_loja_padrao(db) or obter_ou_criar_loja(
        db, nome="Loja Board", tipo_conector="supabase", config_conector={}
    )
    loja_id = loja.id
    db.close()
    return loja_id


def test_board_view_renders_and_groups_leads_by_manual_status():
    loja_id = _loja_id()
    _make_lead(loja_id, "5544900000201@c.us", "agendado", nome="Fulano Agendado")
    _make_lead(loja_id, "5544900000202@c.us", "novo", nome="Fulano Novo")

    client = _logged_in_client()
    resp = client.get("/admin/leads?view=quadro")

    assert resp.status_code == 200
    assert "Fulano Agendado" in resp.text
    # "novo" não é um dos STATUS_LEAD_MANUAIS — não deve aparecer em nenhuma coluna do board
    assert "Fulano Novo" not in resp.text
    for status in STATUS_LEAD_MANUAIS:
        assert f'data-status="{status}"' in resp.text


def test_move_lead_status_updates_manual_status():
    loja_id = _loja_id()
    lead_id = _make_lead(loja_id, "5544900000203@c.us", "agendado")

    client = _logged_in_client()
    resp = client.post(f"/admin/leads/{lead_id}/status/mover", json={"status": "convertido"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    db = SessionLocal()
    lead = db.get(Lead, lead_id)
    assert lead.status == "convertido"
    db.close()


def test_move_lead_status_rejects_automatic_statuses():
    loja_id = _loja_id()
    lead_id = _make_lead(loja_id, "5544900000204@c.us", "agendado")

    client = _logged_in_client()
    for status in ("novo", "qualificado"):
        resp = client.post(f"/admin/leads/{lead_id}/status/mover", json={"status": status})
        assert resp.status_code == 400

    db = SessionLocal()
    lead = db.get(Lead, lead_id)
    assert lead.status == "agendado"  # não mudou
    db.close()


def test_move_lead_status_requires_login():
    loja_id = _loja_id()
    lead_id = _make_lead(loja_id, "5544900000205@c.us", "agendado")

    client = TestClient(app)
    resp = client.post(f"/admin/leads/{lead_id}/status/mover", json={"status": "convertido"})

    assert resp.status_code == 401
    db = SessionLocal()
    lead = db.get(Lead, lead_id)
    assert lead.status == "agendado"  # não mudou
    db.close()


def test_move_lead_status_unknown_lead_returns_404():
    client = _logged_in_client()
    resp = client.post("/admin/leads/999999/status/mover", json={"status": "convertido"})
    assert resp.status_code == 404


def test_leads_filter_by_name():
    loja_id = _loja_id()
    _make_lead(loja_id, "5544900000301@c.us", "agendado", nome="Roberto Carlos Filtro")
    _make_lead(loja_id, "5544900000302@c.us", "agendado", nome="Outra Pessoa Filtro")

    client = _logged_in_client()
    resp = client.get("/admin/leads?q=roberto")

    assert resp.status_code == 200
    assert "Roberto Carlos Filtro" in resp.text
    assert "Outra Pessoa Filtro" not in resp.text


def test_leads_filter_by_vehicle():
    loja_id = _loja_id()
    _make_lead(
        loja_id, "5544900000303@c.us", "agendado",
        nome="Cliente A Filtro", veiculo_interesse="Porsche Macan Filtro",
    )
    _make_lead(
        loja_id, "5544900000304@c.us", "agendado",
        nome="Cliente B Filtro", veiculo_interesse="BMW X5 Filtro",
    )

    client = _logged_in_client()
    resp = client.get("/admin/leads?q=macan")

    assert resp.status_code == 200
    assert "Cliente A Filtro" in resp.text
    assert "Cliente B Filtro" not in resp.text


def test_leads_filter_works_on_board_view_too():
    loja_id = _loja_id()
    _make_lead(loja_id, "5544900000305@c.us", "agendado", nome="Board Filtro Achar")
    _make_lead(loja_id, "5544900000306@c.us", "contatado", nome="Board Filtro Ignorar")

    client = _logged_in_client()
    resp = client.get("/admin/leads?view=quadro&q=achar")

    assert resp.status_code == 200
    assert "Board Filtro Achar" in resp.text
    assert "Board Filtro Ignorar" not in resp.text


def test_leads_resultados_returns_filtered_fragment_without_page_layout():
    """Regressão: o filtro em tempo real (leads.html) busca esse endpoint via fetch e troca o
    innerHTML de #results — não pode vir com <html>/<nav> junto, só o fragmento da tabela/board."""
    loja_id = _loja_id()
    _make_lead(loja_id, "5544900000307@c.us", "agendado", nome="Fragmento Achar")
    _make_lead(loja_id, "5544900000308@c.us", "agendado", nome="Fragmento Ignorar")

    client = _logged_in_client()
    resp = client.get("/admin/leads/resultados?q=achar")

    assert resp.status_code == 200
    assert "Fragmento Achar" in resp.text
    assert "Fragmento Ignorar" not in resp.text
    assert "<html" not in resp.text
    assert "<nav" not in resp.text


def test_leads_resultados_requires_login():
    client = TestClient(app)
    resp = client.get("/admin/leads/resultados?q=algo")
    assert resp.status_code == 401
