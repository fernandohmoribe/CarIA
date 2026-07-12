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

from image_utils import resize_and_save_webp

from connectors.base import VehicleSourceConnector

_DETAIL_HREF_RE = re.compile(r"^/Veiculo/[^/]+/(\d+)/detalhes")
_TITLE_RE = re.compile(r"^(?P<head>.+?) (?P<year>\d{4}) - (?P<version>.+?) - R\$\s*(?P<price>[\d.,]+)\s*$")

# Fallback caso a descoberta dinâmica (filtro de marca da listagem) não encontre nada —
# marcas já confirmadas no catálogo real da Garcia Multimarcas.
_FALLBACK_BRANDS = [
    "Land Rover", "Chevrolet", "Citroen", "Fiat", "Ford", "Honda",
    "Hyundai", "Nissan", "Renault", "Toyota", "Volkswagen",
]


def _parse_price_brl(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = text.replace("R$", "").strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_year(text: str | None) -> int | None:
    """"2022/2023" (fabricação/modelo) -> 2023 (ano-modelo); "2019" -> 2019."""
    if not text:
        return None
    parts = text.strip().split("/")
    try:
        return int(parts[-1])
    except ValueError:
        return None


def _parse_km(text: str | None) -> int | None:
    if not text:
        return None
    text = text.strip()
    if text.upper() in ("N/I", ""):
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


class AutoCertoVehicleConnector(VehicleSourceConnector):
    def __init__(self, site_url: str, timeout: float = 30.0):
        self.site_url = site_url.rstrip("/")
        self.timeout = timeout
        self._images_cache: dict[str, list[dict]] = {}
        self._known_brands: list[str] | None = None

    # ── Descoberta de marcas (pro split "BRAND MODEL" do título) ──────────────

    def _discover_brands(self, listing_soup: BeautifulSoup) -> list[str]:
        if self._known_brands is not None:
            return self._known_brands

        brands = []
        for a in listing_soup.select('a[href*="marca="]'):
            title = (a.get("title") or a.get_text(strip=True) or "").strip()
            if title and title not in brands:
                brands.append(title)

        self._known_brands = brands or list(_FALLBACK_BRANDS)
        return self._known_brands

    def _split_brand_model(self, head: str) -> tuple[str, str]:
        """head = "BRAND MODEL" em caixa alta. Usa a marca conhecida mais longa que bate
        como prefixo (resolve marcas de duas palavras tipo "LAND ROVER")."""
        head_upper = head.upper()
        candidates = sorted(self._known_brands or _FALLBACK_BRANDS, key=len, reverse=True)
        for brand in candidates:
            prefix = brand.upper()
            if head_upper == prefix or head_upper.startswith(prefix + " "):
                model = head[len(brand):].strip()
                return brand.title(), model.title() if model else model

        # Nenhuma marca conhecida bateu — degrada pra "primeira palavra = marca" em vez
        # de quebrar a sincronização inteira por causa de um veículo.
        parts = head.split(" ", 1)
        brand = parts[0].title()
        model = parts[1].title() if len(parts) > 1 else ""
        return brand, model

    # ── Coleta de URLs de detalhe (com paginação defensiva) ───────────────────

    def _collect_detail_urls(self, client: httpx.Client) -> tuple[list[str], BeautifulSoup]:
        urls: list[str] = []
        seen: set[str] = set()
        first_soup: BeautifulSoup | None = None
        page_url = f"{self.site_url}/Veiculos"
        visited_pages = 0

        while page_url and visited_pages < 20:
            resp = client.get(page_url, timeout=self.timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            if first_soup is None:
                first_soup = soup

            for a in soup.find_all("a", href=True):
                href = a["href"]
                if _DETAIL_HREF_RE.match(href):
                    full = href if href.startswith("http") else f"{self.site_url}{href}"
                    if full not in seen:
                        seen.add(full)
                        urls.append(full)

            next_link = soup.find("a", attrs={"rel": "next"}) or next(
                (a for a in soup.find_all("a") if a.get_text(strip=True) in ("Próxima", "próxima", "»", "Próximo")),
                None,
            )
            if next_link and next_link.get("href"):
                href = next_link["href"]
                page_url = href if href.startswith("http") else f"{self.site_url}{href}"
            else:
                page_url = None
            visited_pages += 1

        return urls, first_soup

    # ── Parsing da página de detalhe ───────────────────────────────────────────

    def _parse_detail_page(self, html: str, url: str) -> tuple[dict, list[dict]]:
        soup = BeautifulSoup(html, "lxml")

        match = _DETAIL_HREF_RE.match(url) or re.search(r"/(\d+)/detalhes", url)
        external_id = match.group(1) if match else url

        # O texto de slug da URL sozinho NÃO é garantidamente único — dois anúncios
        # diferentes (ids diferentes) podem ter o mesmo texto (ex: dois veículos com specs
        # idênticos gerando o mesmo slug), o que colidiria no upsert (que é keyed por
        # dealership_id+slug) e sobrescreveria um veículo com o outro silenciosamente. O
        # external_id (sempre único, vem do próprio AutoCerto) resolve isso.
        slug_match = re.search(r"/Veiculo/([^/]+)/\d+/detalhes", url)
        url_slug = slug_match.group(1) if slug_match else external_id
        slug = f"{url_slug}-{external_id}"

        brand = model = version = None
        year = price = None

        title_tag = soup.find("title")
        title_text = title_tag.get_text(strip=True) if title_tag else ""
        title_text = title_text.split(":", 1)[-1].strip() if ":" in title_text else title_text
        title_match = _TITLE_RE.match(title_text)
        if title_match:
            brand, model = self._split_brand_model(title_match.group("head").strip())
            year = _parse_year(title_match.group("year"))
            version = title_match.group("version").strip().title()
            price = _parse_price_brl(title_match.group("price"))

        preco_div = soup.select_one("div.precoVeiculo strong")
        if preco_div:
            parsed = _parse_price_brl(preco_div.get_text(strip=True))
            if parsed is not None:
                price = parsed

        transmission = fuel = None
        mileage = None
        for li in soup.select("ul.listadados li.col5"):
            label_el = li.select_one("span.info")
            value_el = li.select_one("span.info_destaque")
            if not label_el or not value_el:
                continue
            label = label_el.get_text(strip=True).lower()
            value = value_el.get_text(strip=True)
            if label == "ano":
                year = _parse_year(value) or year
            elif label in ("câmbio", "cambio"):
                transmission = value
            elif label in ("combustível", "combustivel"):
                fuel = value
            elif label == "km":
                mileage = _parse_km(value)

        overview_el = soup.select_one("div#vehicle-overview")
        overview = None
        if overview_el:
            paragraphs = [p.get_text(" ", strip=True) for p in overview_el.find_all("p")]
            overview = "\n".join(p for p in paragraphs if p) or overview_el.get_text(" ", strip=True) or None

        highlights: list[str] = []
        features_div = soup.select_one("div#vehicle-add-features")
        if features_div:
            lists = features_div.select("ul.add-features-list")
            if lists:
                highlights.extend(li.get_text(strip=True) for li in lists[0].select("li"))
            if len(lists) > 1:
                extras = [li.get_text(strip=True) for li in lists[1].select("li")]
                highlights.extend(extras[:4])
            highlights = highlights[:6]

        image_urls: list[str] = []
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("src") or ""
            if "autocerto.com/fotos/" in src and src not in image_urls:
                image_urls.append(src)

        images = [
            {"image_url": u, "is_cover": i == 0, "sort_order": i}
            for i, u in enumerate(image_urls)
        ]

        vehicle = {
            "external_id": external_id,
            "slug": slug,
            "code": None,
            "brand": brand,
            "model": model,
            "version": version,
            "year": year,
            "price": price,
            "mileage": mileage,
            "status": "Disponivel",
            "publication_status": "Publicado",
            "body": None,
            "transmission": transmission,
            "fuel": fuel,
            "color": None,
            "spec": None,
            "overview": overview,
            "highlights": highlights,
            "cover_image_url": image_urls[0] if image_urls else None,
        }
        return vehicle, images

    # ── Interface pública (VehicleSourceConnector) ─────────────────────────────

    def fetch_vehicles(self) -> list[dict]:
        vehicles: list[dict] = []
        with httpx.Client(headers={"User-Agent": "Mozilla/5.0 (compatible; CarIA-sync/1.0)"}) as client:
            detail_urls, listing_soup = self._collect_detail_urls(client)
            if listing_soup is not None:
                self._discover_brands(listing_soup)

            # Sequencial de propósito: é um job em lote (nunca no caminho de uma requisição
            # HTTP do bot), e ~50 páginas não justifica a complexidade de paralelismo — dá
            # pra paralelizar depois (como _download_all_images já faz) se o estoque crescer
            # muito além disso.
            for url in detail_urls:
                resp = client.get(url, timeout=self.timeout)
                resp.raise_for_status()
                vehicle, images = self._parse_detail_page(resp.text, url)
                self._images_cache[vehicle["external_id"]] = images
                vehicles.append(vehicle)

        return vehicles

    def fetch_images(self, external_ids: list[str]) -> dict[str, list[dict]]:
        """Requer que fetch_vehicles() já tenha rodado nessa mesma instância — é sempre o
        caso no fluxo de sync_inventory.py, que cria um conector novo e chama os dois
        métodos em sequência. Chamar isso isoladamente numa instância nova retorna vazio
        em vez de quebrar."""
        result: dict[str, list[dict]] = defaultdict(list)
        for eid in external_ids:
            result[eid] = self._images_cache.get(eid, [])
        return dict(result)

    def download_image(self, image_url: str, dest_path, width: int = 1000, height: int = 750, quality: int = 78) -> bool:
        """AutoCerto não tem endpoint de transform de imagem (diferente do Supabase
        Storage) — baixa o JPEG original e redimensiona/converte pra WebP no cliente."""
        try:
            resp = httpx.get(image_url, timeout=self.timeout)
            resp.raise_for_status()
            resize_and_save_webp(resp.content, dest_path, width, height, quality)
            return True
        except (httpx.HTTPError, OSError):
            return False
