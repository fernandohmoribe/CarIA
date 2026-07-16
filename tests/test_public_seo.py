import json
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

import public.routes as public_routes
import template_helpers
from database import SessionLocal, Veiculo, obter_loja_padrao, obter_ou_criar_loja
from main import app


def _loja_id():
    db = SessionLocal()
    loja = obter_loja_padrao(db) or obter_ou_criar_loja(
        db, nome="Loja SEO Publico", tipo_conector="supabase", config_conector={}
    )
    loja_id = loja.id
    db.close()
    return loja_id


def _make_veiculo(loja_id, slug, **overrides):
    db = SessionLocal()
    data = dict(
        loja_id=loja_id, slug=slug, marca="Fiat", modelo="Mobi", versao="1.0 Like", ano=2020,
        preco=60000.0, quilometragem=30000, cambio="Manual", combustivel="Flex", cor="Branco",
        carroceria="Hatch", status="Disponivel", status_publicacao="Publicado",
        destaques_json='["IPVA Pago", "Ar condicionado"]',
        descricao="TEXTO GENERICO REPETIDO EM TODO O ESTOQUE, Astra, Vectra, Meriva...",
    )
    data.update(overrides)
    veiculo = Veiculo(**data)
    db.add(veiculo)
    db.commit()
    db.close()
    return slug


# ── descricao_veiculo (unit, sem HTTP) ──────────────────────────────────────
def test_descricao_veiculo_monta_texto_unico_a_partir_de_campos_estruturados():
    loja_id = _loja_id()
    slug = _make_veiculo(loja_id, "seo-descricao-unica")
    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.slug == slug).first()
    texto = template_helpers.descricao_veiculo(veiculo)
    db.close()
    assert "Fiat Mobi" in texto
    assert "2020" in texto
    assert "30.000 km" in texto
    assert "IPVA Pago" in texto
    assert "TEXTO GENERICO" not in texto  # não usa a coluna raspada


def test_descricao_veiculo_trunca_na_ultima_palavra_inteira():
    loja_id = _loja_id()
    slug = _make_veiculo(loja_id, "seo-descricao-truncada")
    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.slug == slug).first()
    texto = template_helpers.descricao_veiculo(veiculo, max_chars=40)
    db.close()
    assert len(texto) <= 40
    assert not texto.rstrip("…").endswith(" ")


def test_descricao_veiculo_omite_campos_ausentes_sem_none_literal():
    loja_id = _loja_id()
    slug = _make_veiculo(loja_id, "seo-descricao-campo-ausente", cor=None, destaques_json="[]")
    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.slug == slug).first()
    texto = template_helpers.descricao_veiculo(veiculo)
    db.close()
    assert "None" not in texto


# ── json_ld_veiculo / json_ld_loja (unit) ───────────────────────────────────
class _RequestFake:
    class _URL:
        def __str__(self):
            return "http://testserver/"

    base_url = _URL()


def test_json_ld_veiculo_e_json_valido_com_tipo_vehicle():
    loja_id = _loja_id()
    slug = _make_veiculo(loja_id, "seo-json-ld-veiculo")
    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.slug == slug).first()
    bruto = template_helpers.json_ld_veiculo(veiculo, _RequestFake())
    db.close()
    dados = json.loads(bruto)
    assert dados["@type"] == "Vehicle"
    assert dados["brand"] == "Fiat"
    assert dados["offers"]["price"] == 60000.0
    assert dados["offers"]["priceCurrency"] == "BRL"


def test_json_ld_loja_e_json_valido_com_tipo_autodealer():
    bruto = template_helpers.json_ld_loja(_RequestFake())
    dados = json.loads(bruto)
    assert dados["@type"] == "AutoDealer"
    assert "name" in dados


# ── url_absoluta (unit) ──────────────────────────────────────────────────
def test_url_absoluta_monta_url_a_partir_do_path():
    assert template_helpers.url_absoluta(_RequestFake(), "/veiculos/algum-slug") == "http://testserver/veiculos/algum-slug"


def test_url_absoluta_passa_direto_valor_ja_absoluto():
    assert template_helpers.url_absoluta(_RequestFake(), "https://exemplo.com/foto.jpg") == "https://exemplo.com/foto.jpg"
    assert template_helpers.url_absoluta(_RequestFake(), "data:image/gif;base64,xyz") == "data:image/gif;base64,xyz"


# ── páginas públicas: meta description / OG / JSON-LD renderizados ─────────
def test_vehicle_detail_page_has_unique_meta_description_not_boilerplate():
    loja_id = _loja_id()
    slug = _make_veiculo(loja_id, "seo-ficha-meta-descricao")
    client = TestClient(app)
    resp = client.get(f"/veiculos/{slug}")
    assert resp.status_code == 200
    assert "TEXTO GENERICO REPETIDO" not in resp.text
    assert 'name="description"' in resp.text
    assert "Fiat Mobi" in resp.text.split("<body")[0]  # aparece na <head> (title/meta/og)


def test_vehicle_detail_page_has_open_graph_tags():
    loja_id = _loja_id()
    slug = _make_veiculo(loja_id, "seo-ficha-og")
    client = TestClient(app)
    resp = client.get(f"/veiculos/{slug}")
    assert 'property="og:title"' in resp.text
    assert 'property="og:image"' in resp.text
    assert 'property="og:url"' in resp.text


def test_vehicle_detail_page_has_valid_json_ld():
    loja_id = _loja_id()
    slug = _make_veiculo(loja_id, "seo-ficha-jsonld")
    client = TestClient(app)
    resp = client.get(f"/veiculos/{slug}")
    blocos = resp.text.split('type="application/ld+json">')[1:]
    assert len(blocos) >= 2  # loja (sitewide) + veículo
    tipos = set()
    for bloco in blocos:
        bruto = bloco.split("</script>")[0]
        tipos.add(json.loads(bruto)["@type"])
    assert "AutoDealer" in tipos
    assert "Vehicle" in tipos


def test_home_and_catalog_have_canonical_and_meta_description():
    client = TestClient(app)
    for caminho in ("/", "/veiculos"):
        resp = client.get(caminho)
        assert resp.status_code == 200
        assert 'rel="canonical"' in resp.text
        assert 'name="description"' in resp.text


def test_favoritos_page_has_noindex_robots_meta():
    client = TestClient(app)
    resp = client.get("/favoritos")
    assert 'name="robots" content="noindex,follow"' in resp.text


# ── analytics condicional (env var vazia = nada renderiza) ─────────────────
def test_analytics_scripts_absent_by_default():
    client = TestClient(app)
    resp = client.get("/")
    assert "googletagmanager.com/gtag" not in resp.text
    assert "connect.facebook.net" not in resp.text


def test_ga4_script_renders_when_measurement_id_configured(monkeypatch):
    monkeypatch.setattr(public_routes, "GA_MEASUREMENT_ID", "G-TESTE123")
    client = TestClient(app)
    resp = client.get("/")
    assert "G-TESTE123" in resp.text
    assert "googletagmanager.com/gtag" in resp.text


def test_meta_pixel_script_renders_when_pixel_id_configured(monkeypatch):
    monkeypatch.setattr(public_routes, "META_PIXEL_ID", "1234567890")
    client = TestClient(app)
    resp = client.get("/")
    assert "1234567890" in resp.text
    assert "connect.facebook.net" in resp.text


# ── sitemap.xml / robots.txt ────────────────────────────────────────────────
def test_sitemap_xml_lists_static_pages_and_published_vehicle():
    loja_id = _loja_id()
    slug = _make_veiculo(loja_id, "seo-sitemap-veiculo-publicado")
    client = TestClient(app)
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")

    raiz = ET.fromstring(resp.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text for el in raiz.findall("sm:url/sm:loc", ns)]
    assert any(loc.endswith("/veiculos") for loc in locs)
    assert any(loc.endswith(f"/veiculos/{slug}") for loc in locs)
    assert not any("/favoritos" in loc for loc in locs)


def test_sitemap_xml_excludes_hidden_and_sold_vehicles():
    loja_id = _loja_id()
    slug_vendido = _make_veiculo(loja_id, "seo-sitemap-vendido", status="Vendido")
    slug_rascunho = _make_veiculo(loja_id, "seo-sitemap-rascunho", status_publicacao="Rascunho")
    client = TestClient(app)
    resp = client.get("/sitemap.xml")
    assert slug_vendido not in resp.text
    assert slug_rascunho not in resp.text


def test_robots_txt_disallows_admin_and_points_to_sitemap():
    client = TestClient(app)
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert "Disallow: /admin" in resp.text
    assert "Sitemap:" in resp.text
    assert "/sitemap.xml" in resp.text
