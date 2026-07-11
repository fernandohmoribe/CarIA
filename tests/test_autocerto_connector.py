from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from connectors.autocerto_connector import (
    AutoCertoVehicleConnector,
    _parse_km,
    _parse_price_brl,
    _parse_year,
)

FIXTURES = Path(__file__).parent / "fixtures" / "autocerto"

SITE_URL = "https://garciamultimarcasmga.com.br"
NIVUS_URL = f"{SITE_URL}/Veiculo/nivus-1.0-200-tsi-total-flex-highline-automatico-flex-2023/5130327/detalhes"
DISCOVERY_URL = (
    f"{SITE_URL}/Veiculo/discovery-sport-2.2-16v-sd4-turbo-diesel-se-4p-automatico-diesel-2016/5023853/detalhes"
)

_FIXTURE_BY_URL = {
    f"{SITE_URL}/Veiculos": FIXTURES / "listagem.html",
    NIVUS_URL: FIXTURES / "detalhe_nivus.html",
    DISCOVERY_URL: FIXTURES / "detalhe_discovery_sport.html",
}


def _fake_get(self, url, **kwargs):
    path = _FIXTURE_BY_URL.get(url)
    if path is None:
        raise AssertionError(f"URL não esperada no teste (sem fixture): {url}")
    resp = MagicMock(spec=httpx.Response)
    resp.text = path.read_text(encoding="utf-8")
    resp.raise_for_status = lambda: None
    return resp


@pytest.fixture(autouse=True)
def _mock_http(monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", _fake_get)


# ── Parsers isolados ──────────────────────────────────────────────────────────

def test_parse_price_brl():
    assert _parse_price_brl("R$ 116.900,00") == 116900.0
    assert _parse_price_brl(None) is None
    assert _parse_price_brl("") is None


def test_parse_year_usa_ano_modelo():
    assert _parse_year("2022/2023") == 2023
    assert _parse_year("2019") == 2019
    assert _parse_year(None) is None


def test_parse_km_ni_vira_none():
    assert _parse_km("N/I") is None
    assert _parse_km("45.320") == 45320
    assert _parse_km(None) is None


# ── Split marca/modelo ─────────────────────────────────────────────────────────

def test_split_brand_model_marca_de_duas_palavras():
    connector = AutoCertoVehicleConnector(site_url=SITE_URL)
    connector._known_brands = ["Land Rover", "Toyota"]
    brand, model = connector._split_brand_model("LAND ROVER DISCOVERY SPORT")
    assert brand == "Land Rover"
    assert model == "Discovery Sport"


def test_split_brand_model_marca_de_uma_palavra():
    connector = AutoCertoVehicleConnector(site_url=SITE_URL)
    connector._known_brands = ["Land Rover", "Volkswagen"]
    brand, model = connector._split_brand_model("VOLKSWAGEN NIVUS")
    assert brand == "Volkswagen"
    assert model == "Nivus"


def test_slug_inclui_external_id_pra_nao_colidir_com_anuncio_de_texto_igual():
    """Dois anúncios reais no site podem ter o mesmo texto de slug (ex: dois veículos com
    specs idênticos) — sem o external_id, o segundo sobrescreveria o primeiro no upsert
    (keyed por dealership_id+slug)."""
    connector = AutoCertoVehicleConnector(site_url=SITE_URL)
    connector._known_brands = ["Volkswagen"]
    html = (FIXTURES / "detalhe_nivus.html").read_text(encoding="utf-8")

    url_a = f"{SITE_URL}/Veiculo/mesmo-slug-texto/1111111/detalhes"
    url_b = f"{SITE_URL}/Veiculo/mesmo-slug-texto/2222222/detalhes"
    vehicle_a, _ = connector._parse_detail_page(html, url_a)
    vehicle_b, _ = connector._parse_detail_page(html, url_b)

    assert vehicle_a["slug"] != vehicle_b["slug"]
    assert vehicle_a["slug"] == "mesmo-slug-texto-1111111"
    assert vehicle_b["slug"] == "mesmo-slug-texto-2222222"


# ── fetch_vehicles / fetch_images end-to-end contra fixtures ──────────────────

def test_fetch_vehicles_retorna_dados_normalizados():
    connector = AutoCertoVehicleConnector(site_url=SITE_URL)
    vehicles = connector.fetch_vehicles()

    assert len(vehicles) == 2
    by_id = {v["external_id"]: v for v in vehicles}

    nivus = by_id["5130327"]
    # Slug inclui o external_id — o texto puro da URL sozinho não é garantidamente único
    # (dois anúncios diferentes no site real têm o mesmo texto de slug, ver connector).
    assert nivus["slug"] == "nivus-1.0-200-tsi-total-flex-highline-automatico-flex-2023-5130327"
    assert nivus["brand"] == "Volkswagen"
    assert nivus["model"] == "Nivus"
    assert nivus["year"] == 2023
    assert nivus["price"] == 116900.0
    assert nivus["transmission"] == "Automático"
    assert nivus["fuel"] == "Flex"
    assert nivus["mileage"] is None  # "N/I" no fixture
    assert nivus["status"] == "Disponivel"
    assert nivus["publication_status"] == "Publicado"
    assert nivus["cover_image_url"]
    assert "autocerto.com/fotos/" in nivus["cover_image_url"]

    discovery = by_id["5023853"]
    assert discovery["brand"] == "Land Rover"
    assert discovery["model"] == "Discovery Sport"
    assert discovery["mileage"] == 159100


def test_fetch_vehicles_curadoria_de_destaques():
    connector = AutoCertoVehicleConnector(site_url=SITE_URL)
    vehicles = connector.fetch_vehicles()
    nivus = next(v for v in vehicles if v["external_id"] == "5130327")

    # Características (1 item real no fixture: "IPVA Pago") + até 4 de Opcionais, capado em 6.
    assert nivus["highlights"][0] == "IPVA Pago"
    assert len(nivus["highlights"]) <= 6
    assert len(nivus["highlights"]) == 5  # 1 característica + 4 opcionais


def test_fetch_vehicles_fotos_em_ordem_sem_duplicar():
    connector = AutoCertoVehicleConnector(site_url=SITE_URL)
    vehicles = connector.fetch_vehicles()
    nivus = next(v for v in vehicles if v["external_id"] == "5130327")
    images = connector.fetch_images(["5130327"])["5130327"]

    assert len(images) == 12  # 12 fotos no fixture, sem duplicar (li + img com mesmo data-src)
    assert images[0]["is_cover"] is True
    assert images[0]["sort_order"] == 0
    assert images[1]["is_cover"] is False
    assert nivus["cover_image_url"] == images[0]["image_url"]


def test_fetch_images_usa_cache_sem_bater_de_novo(monkeypatch):
    connector = AutoCertoVehicleConnector(site_url=SITE_URL)
    connector.fetch_vehicles()

    call_count = {"n": 0}
    original = httpx.Client.get

    def _counting_get(self, url, **kwargs):
        call_count["n"] += 1
        return original(self, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "get", _counting_get)
    images = connector.fetch_images(["5130327", "5023853"])

    assert call_count["n"] == 0  # não bateu de novo, usou o cache populado por fetch_vehicles
    assert len(images["5130327"]) == 12
    assert len(images["5023853"]) == 14


def test_fetch_images_sem_fetch_vehicles_antes_retorna_vazio():
    """Uso indevido (chamar fetch_images numa instância nova) não quebra, só retorna vazio —
    documentado no docstring do método."""
    connector = AutoCertoVehicleConnector(site_url=SITE_URL)
    assert connector.fetch_images(["5130327"]) == {"5130327": []}
