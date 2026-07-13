from fastapi.testclient import TestClient

from database import SessionLocal, Veiculo, obter_loja_padrao, obter_ou_criar_loja
from main import app


def _logged_in_client() -> TestClient:
    client = TestClient(app)
    client.post("/admin/login", data={"nome_usuario": "admin", "senha": "test-password"})
    return client


def _loja_id():
    db = SessionLocal()
    loja = obter_loja_padrao(db) or obter_ou_criar_loja(
        db, nome="Loja Catalogo Publico", tipo_conector="supabase", config_conector={}
    )
    loja_id = loja.id
    db.close()
    return loja_id


def _make_veiculo(loja_id, slug, marca, modelo, status="Disponivel", status_publicacao="Publicado"):
    db = SessionLocal()
    veiculo = Veiculo(
        loja_id=loja_id, slug=slug, marca=marca, modelo=modelo, ano=2022, preco=90000.0,
        status=status, status_publicacao=status_publicacao,
    )
    db.add(veiculo)
    db.commit()
    db.close()
    return slug


def test_public_catalog_lists_published_available_vehicle():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "catalogo-publico-disponivel", "Fiat", "Mobi Catalogo Publico")

    client = TestClient(app)
    resp = client.get("/veiculos")
    assert resp.status_code == 200
    assert "Mobi Catalogo Publico" in resp.text


def test_public_catalog_hides_sold_and_draft_vehicles_but_admin_still_sees_them():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "catalogo-vendido-oculto", "Fiat", "Mobi Vendido Oculto", status="Vendido")
    _make_veiculo(loja_id, "catalogo-rascunho-oculto", "Fiat", "Mobi Rascunho Oculto", status_publicacao="Rascunho")

    public_client = TestClient(app)
    resp = public_client.get("/veiculos")
    assert "Mobi Vendido Oculto" not in resp.text
    assert "Mobi Rascunho Oculto" not in resp.text

    admin_client = _logged_in_client()
    resp = admin_client.get("/admin/veiculos")
    assert "Mobi Vendido Oculto" in resp.text
    assert "Mobi Rascunho Oculto" in resp.text


def test_public_vehicle_detail_200_for_published_404_for_hidden():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "catalogo-detalhe-publicado", "Renault", "Sandero Detalhe Publico")
    _make_veiculo(loja_id, "catalogo-detalhe-oculto", "Renault", "Sandero Detalhe Oculto", status="Vendido")

    client = TestClient(app)
    resp = client.get("/veiculos/catalogo-detalhe-publicado")
    assert resp.status_code == 200
    assert "Sandero Detalhe Publico" in resp.text

    resp = client.get("/veiculos/catalogo-detalhe-oculto")
    assert resp.status_code == 404

    resp = client.get("/veiculos/slug-que-nao-existe-em-lugar-nenhum")
    assert resp.status_code == 404


def test_submit_interest_form_creates_lead_visible_in_admin():
    from database import obter_todos_leads

    loja_id = _loja_id()
    slug = _make_veiculo(loja_id, "catalogo-interesse-cria-lead", "Toyota", "Corolla Interesse Teste")

    client = TestClient(app)
    resp = client.post(
        f"/veiculos/{slug}/interesse",
        data={"nome": "Cliente Site", "telefone": "(44) 91234-5678", "email": "cliente@teste.com"},
    )
    assert resp.status_code == 200
    assert "Recebemos seu interesse" in resp.text

    db = SessionLocal()
    leads = obter_todos_leads(db, loja_id)
    db.close()
    match = [l for l in leads if l.telefone == "(44) 91234-5678"]
    assert len(match) == 1
    lead = match[0]
    assert lead.origem == "site"
    assert lead.nome == "Cliente Site"
    assert lead.veiculo_interesse == "Toyota Corolla Interesse Teste"
    assert lead.veiculo_slug == slug


def test_submit_interest_twice_same_phone_updates_same_lead():
    from database import obter_todos_leads

    loja_id = _loja_id()
    slug = _make_veiculo(loja_id, "catalogo-interesse-dedup", "Honda", "Civic Interesse Dedup")

    client = TestClient(app)
    client.post(f"/veiculos/{slug}/interesse", data={"nome": "Primeiro Nome", "telefone": "44 98888-1111"})
    client.post(f"/veiculos/{slug}/interesse", data={"nome": "Nome Atualizado", "telefone": "(44) 988881111"})

    db = SessionLocal()
    leads = obter_todos_leads(db, loja_id)
    db.close()
    match = [l for l in leads if l.numero_telefone == "44988881111"]
    assert len(match) == 1
    assert match[0].nome == "Nome Atualizado"


def test_interest_form_rate_limited_after_too_many_submissions():
    loja_id = _loja_id()
    slug = _make_veiculo(loja_id, "catalogo-interesse-rate-limit", "Fiat", "Argo Rate Limit Teste")

    client = TestClient(app)
    last_status = None
    for i in range(7):
        resp = client.post(
            f"/veiculos/{slug}/interesse",
            data={"nome": f"Spam {i}", "telefone": f"4499999{i:04d}"},
        )
        last_status = resp.status_code
    assert last_status == 429


def test_catalog_uses_real_logo_asset():
    client = TestClient(app)
    resp = client.get("/veiculos")
    assert resp.status_code == 200
    assert '<img src="/static/logo.png"' in resp.text

    resp = client.get("/static/logo.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
