from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from connectors.autocerto_connector import (
    ConectorAutoCerto,
    _analisar_ano,
    _analisar_km,
    _analisar_preco_brl,
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
    assert _analisar_preco_brl("R$ 116.900,00") == 116900.0
    assert _analisar_preco_brl(None) is None
    assert _analisar_preco_brl("") is None


def test_parse_year_usa_ano_modelo():
    assert _analisar_ano("2022/2023") == 2023
    assert _analisar_ano("2019") == 2019
    assert _analisar_ano(None) is None


def test_parse_km_ni_vira_none():
    assert _analisar_km("N/I") is None
    assert _analisar_km("45.320") == 45320
    assert _analisar_km(None) is None


# ── Split marca/modelo ─────────────────────────────────────────────────────────

def test_split_brand_model_marca_de_duas_palavras():
    connector = ConectorAutoCerto(site_url=SITE_URL)
    connector._marcas_conhecidas = ["Land Rover", "Toyota"]
    marca, modelo = connector._dividir_marca_modelo("LAND ROVER DISCOVERY SPORT")
    assert marca == "Land Rover"
    assert modelo == "Discovery Sport"


def test_split_brand_model_marca_de_uma_palavra():
    connector = ConectorAutoCerto(site_url=SITE_URL)
    connector._marcas_conhecidas = ["Land Rover", "Volkswagen"]
    marca, modelo = connector._dividir_marca_modelo("VOLKSWAGEN NIVUS")
    assert marca == "Volkswagen"
    assert modelo == "Nivus"


def test_slug_inclui_external_id_pra_nao_colidir_com_anuncio_de_texto_igual():
    """Dois anúncios reais no site podem ter o mesmo texto de slug (ex: dois veículos com
    specs idênticos) — sem o id_externo, o segundo sobrescreveria o primeiro no upsert
    (keyed por loja_id+slug)."""
    connector = ConectorAutoCerto(site_url=SITE_URL)
    connector._marcas_conhecidas = ["Volkswagen"]
    html = (FIXTURES / "detalhe_nivus.html").read_text(encoding="utf-8")

    url_a = f"{SITE_URL}/Veiculo/mesmo-slug-texto/1111111/detalhes"
    url_b = f"{SITE_URL}/Veiculo/mesmo-slug-texto/2222222/detalhes"
    veiculo_a, _ = connector._analisar_pagina_detalhe(html, url_a)
    veiculo_b, _ = connector._analisar_pagina_detalhe(html, url_b)

    assert veiculo_a["slug"] != veiculo_b["slug"]
    assert veiculo_a["slug"] == "mesmo-slug-texto-1111111"
    assert veiculo_b["slug"] == "mesmo-slug-texto-2222222"


# ── buscar_veiculos / buscar_imagens end-to-end contra fixtures ───────────────

def test_fetch_vehicles_retorna_dados_normalizados():
    connector = ConectorAutoCerto(site_url=SITE_URL)
    veiculos = connector.buscar_veiculos()

    assert len(veiculos) == 2
    by_id = {v["id_externo"]: v for v in veiculos}

    nivus = by_id["5130327"]
    # Slug inclui o id_externo — o texto puro da URL sozinho não é garantidamente único
    # (dois anúncios diferentes no site real têm o mesmo texto de slug, ver connector).
    assert nivus["slug"] == "nivus-1.0-200-tsi-total-flex-highline-automatico-flex-2023-5130327"
    assert nivus["marca"] == "Volkswagen"
    assert nivus["modelo"] == "Nivus"
    assert nivus["ano"] == 2023
    assert nivus["preco"] == 116900.0
    assert nivus["cambio"] == "Automático"
    assert nivus["combustivel"] == "Flex"
    assert nivus["quilometragem"] is None  # "N/I" no fixture
    assert nivus["status"] == "Disponivel"
    assert nivus["status_publicacao"] == "Publicado"
    assert nivus["url_imagem_capa"]
    assert "autocerto.com/fotos/" in nivus["url_imagem_capa"]

    discovery = by_id["5023853"]
    assert discovery["marca"] == "Land Rover"
    assert discovery["modelo"] == "Discovery Sport"
    assert discovery["quilometragem"] == 159100


def test_fetch_vehicles_curadoria_de_destaques():
    connector = ConectorAutoCerto(site_url=SITE_URL)
    veiculos = connector.buscar_veiculos()
    nivus = next(v for v in veiculos if v["id_externo"] == "5130327")

    # Características (1 item real no fixture: "IPVA Pago") + até 4 de Opcionais, capado em 6.
    assert nivus["destaques"][0] == "IPVA Pago"
    assert len(nivus["destaques"]) <= 6
    assert len(nivus["destaques"]) == 5  # 1 característica + 4 opcionais


def test_fetch_vehicles_fotos_em_ordem_sem_duplicar():
    connector = ConectorAutoCerto(site_url=SITE_URL)
    veiculos = connector.buscar_veiculos()
    nivus = next(v for v in veiculos if v["id_externo"] == "5130327")
    imagens = connector.buscar_imagens(["5130327"])["5130327"]

    assert len(imagens) == 12  # 12 fotos no fixture, sem duplicar (li + img com mesmo data-src)
    assert imagens[0]["eh_capa"] is True
    assert imagens[0]["ordem"] == 0
    assert imagens[1]["eh_capa"] is False
    assert nivus["url_imagem_capa"] == imagens[0]["url_imagem"]


def test_fetch_images_usa_cache_sem_bater_de_novo(monkeypatch):
    connector = ConectorAutoCerto(site_url=SITE_URL)
    connector.buscar_veiculos()

    call_count = {"n": 0}
    original = httpx.Client.get

    def _counting_get(self, url, **kwargs):
        call_count["n"] += 1
        return original(self, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "get", _counting_get)
    imagens = connector.buscar_imagens(["5130327", "5023853"])

    assert call_count["n"] == 0  # não bateu de novo, usou o cache populado por buscar_veiculos
    assert len(imagens["5130327"]) == 12
    assert len(imagens["5023853"]) == 14


def test_fetch_images_sem_fetch_vehicles_antes_retorna_vazio():
    """Uso indevido (chamar buscar_imagens numa instância nova) não quebra, só retorna vazio —
    documentado no docstring do método."""
    connector = ConectorAutoCerto(site_url=SITE_URL)
    assert connector.buscar_imagens(["5130327"]) == {"5130327": []}
