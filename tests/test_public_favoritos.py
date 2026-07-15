from fastapi.testclient import TestClient

from database import SessionLocal, Veiculo, obter_loja_padrao, obter_ou_criar_loja
from main import app


def _loja_id():
    db = SessionLocal()
    loja = obter_loja_padrao(db) or obter_ou_criar_loja(
        db, nome="Loja Favoritos Publico", tipo_conector="supabase", config_conector={}
    )
    loja_id = loja.id
    db.close()
    return loja_id


def _make_veiculo(loja_id, slug, marca="Fiat", modelo="Mobi", status="Disponivel", status_publicacao="Publicado"):
    db = SessionLocal()
    veiculo = Veiculo(
        loja_id=loja_id, slug=slug, marca=marca, modelo=modelo, ano=2022, preco=90000.0,
        status=status, status_publicacao=status_publicacao,
    )
    db.add(veiculo)
    db.commit()
    db.close()
    return slug


def test_favoritos_page_loads():
    client = TestClient(app)
    resp = client.get("/favoritos")
    assert resp.status_code == 200
    assert 'id="favoritos-grid"' in resp.text
    assert 'id="favoritos-vazio"' in resp.text


def test_api_favoritos_returns_matching_published_vehicles():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "api-favorito-um", "Toyota", "Corolla Api Favorito Um")
    _make_veiculo(loja_id, "api-favorito-dois", "Honda", "Civic Api Favorito Dois")

    client = TestClient(app)
    resp = client.get("/api/favoritos", params={"slugs": "api-favorito-um,api-favorito-dois"})
    assert resp.status_code == 200
    dados = resp.json()
    assert len(dados) == 2
    slugs_retornados = {v["slug"] for v in dados}
    assert slugs_retornados == {"api-favorito-um", "api-favorito-dois"}
    assert dados[0]["marca"] in ("Toyota", "Honda")


def test_api_favoritos_excludes_sold_or_hidden_even_if_slug_requested():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "api-favorito-vendido", status="Vendido")

    client = TestClient(app)
    resp = client.get("/api/favoritos", params={"slugs": "api-favorito-vendido"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_favoritos_ignores_unknown_slugs():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "api-favorito-real", "Fiat", "Mobi Api Favorito Real")

    client = TestClient(app)
    resp = client.get("/api/favoritos", params={"slugs": "api-favorito-real,slug-que-nao-existe"})
    assert resp.status_code == 200
    dados = resp.json()
    assert len(dados) == 1
    assert dados[0]["slug"] == "api-favorito-real"


def test_api_favoritos_empty_slugs_returns_empty_list():
    client = TestClient(app)
    resp = client.get("/api/favoritos", params={"slugs": ""})
    assert resp.status_code == 200
    assert resp.json() == []


def test_vehicle_detail_has_favorite_button():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "favorito-ficha-slug", "Fiat", "Mobi Favorito Ficha")

    client = TestClient(app)
    resp = client.get("/veiculos/favorito-ficha-slug")
    assert 'data-favorito-slug="favorito-ficha-slug"' in resp.text
