from fastapi.testclient import TestClient

from database import Lead, MANUAL_LEAD_STATUSES, SessionLocal, get_default_dealership, get_or_create_dealership
from main import app


def _logged_in_client() -> TestClient:
    client = TestClient(app)
    client.post("/admin/login", data={"username": "admin", "password": "test-password"})
    return client


def _make_lead(dealership_id, phone, status, nome="Cliente Teste", veiculo_interesse=None):
    db = SessionLocal()
    lead = Lead(
        dealership_id=dealership_id, phone_number=phone, nome=nome, status=status,
        veiculo_interesse=veiculo_interesse,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    lead_id = lead.id
    db.close()
    return lead_id


def _dealership_id():
    # leads_page (rota /admin/leads) sempre escopa por get_default_dealership() — os leads do
    # teste precisam pertencer a ESSA revenda, não a uma nova, senão não aparecem na página
    # (o banco de teste é compartilhado entre os arquivos da suíte, ver tests/conftest.py).
    db = SessionLocal()
    dealership = get_default_dealership(db) or get_or_create_dealership(
        db, nome="Loja Board", connector_type="supabase", connector_config={}
    )
    dealership_id = dealership.id
    db.close()
    return dealership_id


def test_board_view_renders_and_groups_leads_by_manual_status():
    dealership_id = _dealership_id()
    _make_lead(dealership_id, "5544900000201@c.us", "agendado", nome="Fulano Agendado")
    _make_lead(dealership_id, "5544900000202@c.us", "novo", nome="Fulano Novo")

    client = _logged_in_client()
    resp = client.get("/admin/leads?view=board")

    assert resp.status_code == 200
    assert "Fulano Agendado" in resp.text
    # "novo" não é um dos MANUAL_LEAD_STATUSES — não deve aparecer em nenhuma coluna do board
    assert "Fulano Novo" not in resp.text
    for status in MANUAL_LEAD_STATUSES:
        assert f'data-status="{status}"' in resp.text


def test_move_lead_status_updates_manual_status():
    dealership_id = _dealership_id()
    lead_id = _make_lead(dealership_id, "5544900000203@c.us", "agendado")

    client = _logged_in_client()
    resp = client.post(f"/admin/leads/{lead_id}/status/mover", json={"status": "convertido"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    db = SessionLocal()
    lead = db.get(Lead, lead_id)
    assert lead.status == "convertido"
    db.close()


def test_move_lead_status_rejects_automatic_statuses():
    dealership_id = _dealership_id()
    lead_id = _make_lead(dealership_id, "5544900000204@c.us", "agendado")

    client = _logged_in_client()
    for status in ("novo", "qualificado"):
        resp = client.post(f"/admin/leads/{lead_id}/status/mover", json={"status": status})
        assert resp.status_code == 400

    db = SessionLocal()
    lead = db.get(Lead, lead_id)
    assert lead.status == "agendado"  # não mudou
    db.close()


def test_move_lead_status_requires_login():
    dealership_id = _dealership_id()
    lead_id = _make_lead(dealership_id, "5544900000205@c.us", "agendado")

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
    dealership_id = _dealership_id()
    _make_lead(dealership_id, "5544900000301@c.us", "agendado", nome="Roberto Carlos Filtro")
    _make_lead(dealership_id, "5544900000302@c.us", "agendado", nome="Outra Pessoa Filtro")

    client = _logged_in_client()
    resp = client.get("/admin/leads?q=roberto")

    assert resp.status_code == 200
    assert "Roberto Carlos Filtro" in resp.text
    assert "Outra Pessoa Filtro" not in resp.text


def test_leads_filter_by_vehicle():
    dealership_id = _dealership_id()
    _make_lead(
        dealership_id, "5544900000303@c.us", "agendado",
        nome="Cliente A Filtro", veiculo_interesse="Porsche Macan Filtro",
    )
    _make_lead(
        dealership_id, "5544900000304@c.us", "agendado",
        nome="Cliente B Filtro", veiculo_interesse="BMW X5 Filtro",
    )

    client = _logged_in_client()
    resp = client.get("/admin/leads?q=macan")

    assert resp.status_code == 200
    assert "Cliente A Filtro" in resp.text
    assert "Cliente B Filtro" not in resp.text


def test_leads_filter_works_on_board_view_too():
    dealership_id = _dealership_id()
    _make_lead(dealership_id, "5544900000305@c.us", "agendado", nome="Board Filtro Achar")
    _make_lead(dealership_id, "5544900000306@c.us", "contatado", nome="Board Filtro Ignorar")

    client = _logged_in_client()
    resp = client.get("/admin/leads?view=board&q=achar")

    assert resp.status_code == 200
    assert "Board Filtro Achar" in resp.text
    assert "Board Filtro Ignorar" not in resp.text


def test_leads_resultados_returns_filtered_fragment_without_page_layout():
    """Regressão: o filtro em tempo real (leads.html) busca esse endpoint via fetch e troca o
    innerHTML de #results — não pode vir com <html>/<nav> junto, só o fragmento da tabela/board."""
    dealership_id = _dealership_id()
    _make_lead(dealership_id, "5544900000307@c.us", "agendado", nome="Fragmento Achar")
    _make_lead(dealership_id, "5544900000308@c.us", "agendado", nome="Fragmento Ignorar")

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
