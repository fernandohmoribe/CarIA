from fastapi.testclient import TestClient

from database import (
    GoogleReview,
    InstagramPost,
    NewsPost,
    SessionLocal,
    Vehicle,
    get_default_dealership,
    get_or_create_dealership,
)
from main import app


def _logged_in_client() -> TestClient:
    client = TestClient(app)
    client.post("/admin/login", data={"username": "admin", "password": "test-password"})
    return client


def _dealership_id():
    db = SessionLocal()
    dealership = get_default_dealership(db) or get_or_create_dealership(
        db, nome="Loja Site Publico", connector_type="supabase", connector_config={}
    )
    dealership_id = dealership.id
    db.close()
    return dealership_id


def _make_vehicle(dealership_id, slug, brand="Fiat", model="Mobi", **overrides):
    db = SessionLocal()
    data = dict(
        dealership_id=dealership_id, slug=slug, brand=brand, model=model, year=2022, price=90000.0,
        status="Disponivel", publication_status="Publicado", body="Hatch", transmission="Manual", fuel="Flex",
    )
    data.update(overrides)
    vehicle = Vehicle(**data)
    db.add(vehicle)
    db.commit()
    db.close()
    return slug


# ── Home ─────────────────────────────────────────────────────────────────
def test_home_shows_hero_and_consultor_form():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "INICIAR ATENDIMENTO" in resp.text


def test_home_hides_video_and_review_sections_when_empty():
    client = TestClient(app)
    resp = client.get("/")
    assert "Avaliações no Google" not in resp.text


def test_home_shows_video_section_when_instagram_post_visible():
    dealership_id = _dealership_id()
    db = SessionLocal()
    db.add(InstagramPost(
        dealership_id=dealership_id, media_id="home-video-visivel", media_type="VIDEO",
        media_url="https://example.com/v.mp4", thumbnail_url="https://example.com/thumb.jpg",
        permalink="https://instagram.com/p/xyz", visivel=True,
    ))
    db.commit()
    db.close()

    client = TestClient(app)
    resp = client.get("/")
    assert "thumb.jpg" in resp.text


def test_home_shows_reviews_when_present():
    dealership_id = _dealership_id()
    db = SessionLocal()
    db.add(GoogleReview(
        dealership_id=dealership_id, author_name="Cliente Satisfeito", rating=5,
        text="Ótimo atendimento!", relative_time_description="há 1 semana",
    ))
    db.commit()
    db.close()

    client = TestClient(app)
    resp = client.get("/")
    assert "Avaliações no Google" in resp.text
    assert "Cliente Satisfeito" in resp.text


def test_consultor_form_creates_lead_and_redirects_to_whatsapp():
    client = TestClient(app)
    resp = client.post(
        "/consultor",
        data={"nome": "Consultor Teste", "telefone": "(44) 99999-1234", "carro": "SUV"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "wa.me" in resp.headers["location"]
    assert "Consultor" in resp.headers["location"] or "Consultor%20Teste" in resp.headers["location"]

    from database import get_all_leads
    db = SessionLocal()
    leads = get_all_leads(db, _dealership_id())
    db.close()
    match = [l for l in leads if l.telefone == "(44) 99999-1234"]
    assert len(match) == 1
    assert match[0].origem == "site"
    assert match[0].veiculo_interesse == "SUV"


def test_consultor_form_requires_nome_and_telefone():
    client = TestClient(app)
    resp = client.post("/consultor", data={"nome": "", "telefone": ""})
    assert resp.status_code == 400


# ── Sobre Nós ────────────────────────────────────────────────────────────
def test_sobre_nos_page_loads():
    client = TestClient(app)
    resp = client.get("/sobre-nos")
    assert resp.status_code == 200
    assert "Sobre a" in resp.text


# ── Contato ──────────────────────────────────────────────────────────────
def test_contato_page_loads():
    client = TestClient(app)
    resp = client.get("/contato")
    assert resp.status_code == 200


def test_contato_form_creates_lead_without_vehicle():
    from database import get_all_leads

    dealership_id = _dealership_id()
    client = TestClient(app)
    resp = client.post(
        "/contato",
        data={"nome": "Contato Teste", "telefone": "44 98888-7777", "mensagem": "Quero saber mais"},
    )
    assert resp.status_code == 200
    assert "Recebemos sua mensagem" in resp.text

    db = SessionLocal()
    leads = get_all_leads(db, dealership_id)
    db.close()
    match = [l for l in leads if l.phone_number == "44988887777"]
    assert len(match) == 1
    assert match[0].veiculo_interesse is None
    assert match[0].origem == "site"


# ── Novidades ────────────────────────────────────────────────────────────
def test_novidades_lists_only_published_posts():
    dealership_id = _dealership_id()
    db = SessionLocal()
    db.add(NewsPost(dealership_id=dealership_id, titulo="Post Publicado Novidade", slug="post-publicado-novidade", publicado=True))
    db.add(NewsPost(dealership_id=dealership_id, titulo="Post Rascunho Novidade", slug="post-rascunho-novidade", publicado=False))
    db.commit()
    db.close()

    client = TestClient(app)
    resp = client.get("/novidades")
    assert "Post Publicado Novidade" in resp.text
    assert "Post Rascunho Novidade" not in resp.text


def test_novidade_detail_200_published_404_draft():
    dealership_id = _dealership_id()
    db = SessionLocal()
    db.add(NewsPost(dealership_id=dealership_id, titulo="Detalhe Publicado", slug="detalhe-publicado-novidade", publicado=True))
    db.add(NewsPost(dealership_id=dealership_id, titulo="Detalhe Rascunho", slug="detalhe-rascunho-novidade", publicado=False))
    db.commit()
    db.close()

    client = TestClient(app)
    resp = client.get("/novidades/detalhe-publicado-novidade")
    assert resp.status_code == 200
    assert "Detalhe Publicado" in resp.text

    resp = client.get("/novidades/detalhe-rascunho-novidade")
    assert resp.status_code == 404


# ── Estoque com filtros ──────────────────────────────────────────────────
def test_catalog_filter_by_marca():
    dealership_id = _dealership_id()
    _make_vehicle(dealership_id, "filtro-marca-toyota", brand="Toyota", model="Corolla Filtro")
    _make_vehicle(dealership_id, "filtro-marca-fiat", brand="Fiat", model="Mobi Filtro")

    client = TestClient(app)
    resp = client.get("/veiculos", params={"marca": "Toyota"})
    assert "Corolla Filtro" in resp.text
    assert "Mobi Filtro" not in resp.text


def test_catalog_filter_by_preco_range():
    dealership_id = _dealership_id()
    _make_vehicle(dealership_id, "filtro-preco-barato", model="Barato Filtro Preco", price=50000.0)
    _make_vehicle(dealership_id, "filtro-preco-caro", model="Caro Filtro Preco", price=200000.0)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"preco_min": "100000"})
    assert "Caro Filtro Preco" in resp.text
    assert "Barato Filtro Preco" not in resp.text


def test_catalog_filter_by_carroceria_cambio_combustivel():
    dealership_id = _dealership_id()
    _make_vehicle(dealership_id, "filtro-suv-auto", model="SUV Filtro Combo", body="SUV", transmission="Automático", fuel="Diesel")
    _make_vehicle(dealership_id, "filtro-hatch-manual", model="Hatch Filtro Combo", body="Hatch", transmission="Manual", fuel="Flex")

    client = TestClient(app)
    resp = client.get("/veiculos", params={"carroceria": "SUV", "cambio": "Automático", "combustivel": "Diesel"})
    assert "SUV Filtro Combo" in resp.text
    assert "Hatch Filtro Combo" not in resp.text


# ── Admin: Novidades CRUD ────────────────────────────────────────────────
def test_admin_novidades_requires_login():
    client = TestClient(app)
    resp = client.get("/admin/novidades", follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_admin_can_create_edit_delete_novidade():
    client = _logged_in_client()

    resp = client.post(
        "/admin/novidades/novo", data={"titulo": "Chegou Novidade Admin", "publicado": "on"}, follow_redirects=False
    )
    assert resp.status_code in (302, 303)

    db = SessionLocal()
    from database import get_all_news_posts
    posts = get_all_news_posts(db, _dealership_id())
    db.close()
    match = [p for p in posts if p.titulo == "Chegou Novidade Admin"]
    assert len(match) == 1
    slug = match[0].slug

    resp = client.get(f"/admin/novidades/{slug}/editar")
    assert resp.status_code == 200
    assert "Chegou Novidade Admin" in resp.text

    resp = client.post(
        f"/admin/novidades/{slug}/editar", data={"titulo": "Novidade Admin Editada", "publicado": "on"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    db = SessionLocal()
    posts = get_all_news_posts(db, _dealership_id())
    db.close()
    assert any(p.titulo == "Novidade Admin Editada" for p in posts)

    resp = client.post(f"/admin/novidades/{slug}/excluir", follow_redirects=False)
    assert resp.status_code in (302, 303)

    db = SessionLocal()
    posts = get_all_news_posts(db, _dealership_id())
    db.close()
    assert not any(p.slug == slug for p in posts)


# ── Admin: curadoria de Instagram ────────────────────────────────────────
def test_admin_instagram_toggle_visibility():
    dealership_id = _dealership_id()
    db = SessionLocal()
    post = InstagramPost(
        dealership_id=dealership_id, media_id="admin-toggle-media", media_type="VIDEO",
        media_url="https://example.com/v2.mp4", permalink="https://instagram.com/p/abc", visivel=False,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    post_id = post.id
    db.close()

    client = _logged_in_client()
    resp = client.post(f"/admin/instagram/{post_id}/visibilidade", data={"visivel": "on"}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    from database import get_visible_instagram_posts
    db = SessionLocal()
    visible = get_visible_instagram_posts(db, dealership_id)
    db.close()
    assert any(p.id == post_id for p in visible)
