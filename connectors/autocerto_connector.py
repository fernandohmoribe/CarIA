"""
Conector de estoque para lojas cujo site roda na plataforma AutoCerto — caso da
Garcia Multimarcas. Diferente do Supabase, o AutoCerto não expõe API pública:
os dados são obtidos via scraping do HTML já renderizado no servidor (sem JS).

A página de detalhe de cada veículo é a fonte de verdade única (marca, modelo,
versão, ano, preço, specs, descrição, destaques, fotos) — a listagem só serve
pra enumerar as URLs de detalhe, porque fotos/specs só existem lá mesmo.
"""

from __future__ import annotations

import re
from collections import defaultdict

import httpx
from bs4 import BeautifulSoup

from image_utils import redimensionar_e_salvar_webp

from connectors.base import ConectorFonteVeiculos

_DETAIL_HREF_RE = re.compile(r"^/Veiculo/[^/]+/(\d+)/detalhes")
_TITLE_RE = re.compile(r"^(?P<head>.+?) (?P<year>\d{4}) - (?P<version>.+?) - R\$\s*(?P<price>[\d.,]+)\s*$")

# Fallback caso a descoberta dinâmica (filtro de marca da listagem) não encontre nada —
# marcas já confirmadas no catálogo real da Garcia Multimarcas.
_MARCAS_FALLBACK = [
    "Land Rover", "Chevrolet", "Citroen", "Fiat", "Ford", "Honda",
    "Hyundai", "Nissan", "Renault", "Toyota", "Volkswagen",
]


def _analisar_preco_brl(texto: str | None) -> float | None:
    if not texto:
        return None
    limpo = texto.replace("R$", "").strip().replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def _analisar_ano(texto: str | None) -> int | None:
    """"2022/2023" (fabricação/modelo) -> 2023 (ano-modelo); "2019" -> 2019."""
    if not texto:
        return None
    partes = texto.strip().split("/")
    try:
        return int(partes[-1])
    except ValueError:
        return None


def _analisar_km(texto: str | None) -> int | None:
    if not texto:
        return None
    texto = texto.strip()
    if texto.upper() in ("N/I", ""):
        return None
    digitos = re.sub(r"[^\d]", "", texto)
    return int(digitos) if digitos else None


class ConectorAutoCerto(ConectorFonteVeiculos):
    def __init__(self, site_url: str, timeout: float = 30.0):
        self.site_url = site_url.rstrip("/")
        self.timeout = timeout
        self._cache_imagens: dict[str, list[dict]] = {}
        self._marcas_conhecidas: list[str] | None = None

    # ── Descoberta de marcas (pro split "MARCA MODELO" do título) ──────────────

    def _descobrir_marcas(self, listing_soup: BeautifulSoup) -> list[str]:
        if self._marcas_conhecidas is not None:
            return self._marcas_conhecidas

        marcas = []
        for a in listing_soup.select('a[href*="marca="]'):
            titulo = (a.get("title") or a.get_text(strip=True) or "").strip()
            if titulo and titulo not in marcas:
                marcas.append(titulo)

        self._marcas_conhecidas = marcas or list(_MARCAS_FALLBACK)
        return self._marcas_conhecidas

    def _dividir_marca_modelo(self, head: str) -> tuple[str, str]:
        """head = "MARCA MODELO" em caixa alta. Usa a marca conhecida mais longa que bate
        como prefixo (resolve marcas de duas palavras tipo "LAND ROVER")."""
        head_upper = head.upper()
        candidatas = sorted(self._marcas_conhecidas or _MARCAS_FALLBACK, key=len, reverse=True)
        for marca in candidatas:
            prefixo = marca.upper()
            if head_upper == prefixo or head_upper.startswith(prefixo + " "):
                modelo = head[len(marca):].strip()
                return marca.title(), modelo.title() if modelo else modelo

        # Nenhuma marca conhecida bateu — degrada pra "primeira palavra = marca" em vez
        # de quebrar a sincronização inteira por causa de um veículo.
        partes = head.split(" ", 1)
        marca = partes[0].title()
        modelo = partes[1].title() if len(partes) > 1 else ""
        return marca, modelo

    # ── Coleta de URLs de detalhe (com paginação defensiva) ───────────────────

    def _coletar_urls_detalhe(self, client: httpx.Client) -> tuple[list[str], BeautifulSoup]:
        urls: list[str] = []
        vistas: set[str] = set()
        primeiro_soup: BeautifulSoup | None = None
        url_pagina = f"{self.site_url}/Veiculos"
        paginas_visitadas = 0

        while url_pagina and paginas_visitadas < 20:
            resp = client.get(url_pagina, timeout=self.timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            if primeiro_soup is None:
                primeiro_soup = soup

            for a in soup.find_all("a", href=True):
                href = a["href"]
                if _DETAIL_HREF_RE.match(href):
                    full = href if href.startswith("http") else f"{self.site_url}{href}"
                    if full not in vistas:
                        vistas.add(full)
                        urls.append(full)

            proximo_link = soup.find("a", attrs={"rel": "next"}) or next(
                (a for a in soup.find_all("a") if a.get_text(strip=True) in ("Próxima", "próxima", "»", "Próximo")),
                None,
            )
            if proximo_link and proximo_link.get("href"):
                href = proximo_link["href"]
                url_pagina = href if href.startswith("http") else f"{self.site_url}{href}"
            else:
                url_pagina = None
            paginas_visitadas += 1

        return urls, primeiro_soup

    # ── Parsing da página de detalhe ───────────────────────────────────────────

    def _analisar_pagina_detalhe(self, html: str, url: str) -> tuple[dict, list[dict]]:
        soup = BeautifulSoup(html, "lxml")

        match = _DETAIL_HREF_RE.match(url) or re.search(r"/(\d+)/detalhes", url)
        id_externo = match.group(1) if match else url

        # O texto de slug da URL sozinho NÃO é garantidamente único — dois anúncios
        # diferentes (ids diferentes) podem ter o mesmo texto (ex: dois veículos com specs
        # idênticos gerando o mesmo slug), o que colidiria no upsert (que é keyed por
        # loja_id+slug) e sobrescreveria um veículo com o outro silenciosamente. O
        # id_externo (sempre único, vem do próprio AutoCerto) resolve isso.
        slug_match = re.search(r"/Veiculo/([^/]+)/\d+/detalhes", url)
        url_slug = slug_match.group(1) if slug_match else id_externo
        slug = f"{url_slug}-{id_externo}"

        marca = modelo = versao = None
        ano = preco = None

        title_tag = soup.find("title")
        texto_titulo = title_tag.get_text(strip=True) if title_tag else ""
        texto_titulo = texto_titulo.split(":", 1)[-1].strip() if ":" in texto_titulo else texto_titulo
        title_match = _TITLE_RE.match(texto_titulo)
        if title_match:
            marca, modelo = self._dividir_marca_modelo(title_match.group("head").strip())
            ano = _analisar_ano(title_match.group("year"))
            versao = title_match.group("version").strip().title()
            preco = _analisar_preco_brl(title_match.group("price"))

        preco_div = soup.select_one("div.precoVeiculo strong")
        if preco_div:
            analisado = _analisar_preco_brl(preco_div.get_text(strip=True))
            if analisado is not None:
                preco = analisado

        cambio = combustivel = None
        quilometragem = None
        for li in soup.select("ul.listadados li.col5"):
            label_el = li.select_one("span.info")
            value_el = li.select_one("span.info_destaque")
            if not label_el or not value_el:
                continue
            label = label_el.get_text(strip=True).lower()
            value = value_el.get_text(strip=True)
            if label == "ano":
                ano = _analisar_ano(value) or ano
            elif label in ("câmbio", "cambio"):
                cambio = value
            elif label in ("combustível", "combustivel"):
                combustivel = value
            elif label == "km":
                quilometragem = _analisar_km(value)

        overview_el = soup.select_one("div#vehicle-overview")
        descricao = None
        if overview_el:
            paragrafos = [p.get_text(" ", strip=True) for p in overview_el.find_all("p")]
            descricao = "\n".join(p for p in paragrafos if p) or overview_el.get_text(" ", strip=True) or None

        destaques: list[str] = []
        features_div = soup.select_one("div#vehicle-add-features")
        if features_div:
            listas = features_div.select("ul.add-features-list")
            if listas:
                destaques.extend(li.get_text(strip=True) for li in listas[0].select("li"))
            if len(listas) > 1:
                extras = [li.get_text(strip=True) for li in listas[1].select("li")]
                destaques.extend(extras[:4])
            destaques = destaques[:6]

        urls_imagem: list[str] = []
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("src") or ""
            if "autocerto.com/fotos/" in src and src not in urls_imagem:
                urls_imagem.append(src)

        imagens = [
            {"url_imagem": u, "eh_capa": i == 0, "ordem": i}
            for i, u in enumerate(urls_imagem)
        ]

        veiculo = {
            "id_externo": id_externo,
            "slug": slug,
            "codigo": None,
            "marca": marca,
            "modelo": modelo,
            "versao": versao,
            "ano": ano,
            "preco": preco,
            "quilometragem": quilometragem,
            "status": "Disponivel",
            "status_publicacao": "Publicado",
            "carroceria": None,
            "cambio": cambio,
            "combustivel": combustivel,
            "cor": None,
            "especificacao": None,
            "descricao": descricao,
            "destaques": destaques,
            "url_imagem_capa": urls_imagem[0] if urls_imagem else None,
        }
        return veiculo, imagens

    # ── Interface pública (ConectorFonteVeiculos) ─────────────────────────────

    def buscar_veiculos(self) -> list[dict]:
        veiculos: list[dict] = []
        with httpx.Client(headers={"User-Agent": "Mozilla/5.0 (compatible; CarIA-sync/1.0)"}) as client:
            urls_detalhe, listing_soup = self._coletar_urls_detalhe(client)
            if listing_soup is not None:
                self._descobrir_marcas(listing_soup)

            # Sequencial de propósito: é um job em lote (nunca no caminho de uma requisição
            # HTTP do bot), e ~50 páginas não justifica a complexidade de paralelismo — dá
            # pra paralelizar depois (como _baixar_todas_imagens já faz) se o estoque crescer
            # muito além disso.
            for url in urls_detalhe:
                resp = client.get(url, timeout=self.timeout)
                resp.raise_for_status()
                veiculo, imagens = self._analisar_pagina_detalhe(resp.text, url)
                self._cache_imagens[veiculo["id_externo"]] = imagens
                veiculos.append(veiculo)

        return veiculos

    def buscar_imagens(self, ids_externos: list[str]) -> dict[str, list[dict]]:
        """Requer que buscar_veiculos() já tenha rodado nessa mesma instância — é sempre o
        caso no fluxo de sync_inventory.py, que cria um conector novo e chama os dois
        métodos em sequência. Chamar isso isoladamente numa instância nova retorna vazio
        em vez de quebrar."""
        resultado: dict[str, list[dict]] = defaultdict(list)
        for id_externo in ids_externos:
            resultado[id_externo] = self._cache_imagens.get(id_externo, [])
        return dict(resultado)

    def baixar_imagem(self, url_imagem: str, caminho_destino, width: int = 1000, height: int = 750, quality: int = 78) -> bool:
        """AutoCerto não tem endpoint de transform de imagem (diferente do Supabase
        Storage) — baixa o JPEG original e redimensiona/converte pra WebP no cliente."""
        try:
            resp = httpx.get(url_imagem, timeout=self.timeout)
            resp.raise_for_status()
            redimensionar_e_salvar_webp(resp.content, caminho_destino, width, height, quality)
            return True
        except (httpx.HTTPError, OSError):
            return False
