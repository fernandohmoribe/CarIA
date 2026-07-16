"""
Filtro/globais de template compartilhados entre admin/routes.py e public/routes.py — cada
um tem sua própria instância de Jinja2Templates (Jinja não compartilha filtro entre
instâncias), mas a lógica em si mora só aqui.
"""

from __future__ import annotations

import json

from dealership_config import DEALERSHIP_ADDRESS, DEALERSHIP_CITY, DEALERSHIP_HOURS, DEALERSHIP_NAME, DEALERSHIP_PHONE


def transformar_url_imagem(url: str, width: int, height: int, quality: int = 65) -> str:
    """Usa a API de transformação de imagem do Supabase pra servir thumbnails leves."""
    marker = "/storage/v1/object/public/"
    idx = url.find(marker) if url else -1
    if idx == -1:
        return url
    base = url[:idx]
    resto = url[idx + len(marker):]
    return f"{base}/storage/v1/render/image/public/{resto}?width={width}&height={height}&resize=cover&quality={quality}"


def brl(value) -> str:
    if value is None:
        return "0,00"
    formatado = f"{value:,.2f}"
    return formatado.replace(",", "X").replace(".", ",").replace("X", ".")


_IMAGEM_EM_BRANCO = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="

# Fotos locais em media/ já vêm em ~1000x750 (tamanho de tela cheia, ver image_utils.py) —
# card de lista/miniatura não precisa disso, e decodificar dezenas delas de uma vez é o que
# deixa o scroll travado. /media-thumb/ (main.py) gera e cacheia uma versão bem menor sob
# demanda — lista fechada de tamanhos de propósito (evita cache-flooding com w/h arbitrário).
TAMANHOS_MINIATURA_LOCAL = {(400, 300)}


def imagem_src(caminho_local: str, url_remota: str, width: int, height: int, quality: int = 65) -> str:
    """Prefere a foto já baixada em media/ — só cai pro Supabase se ainda não tiver sido baixada.
    Sem nenhuma das duas (ex: veículo cadastrado manualmente sem foto), devolve um GIF
    transparente 1x1 em vez de deixar `src="None"` ir pro HTML (link quebrado de verdade)."""
    if caminho_local:
        if (width, height) in TAMANHOS_MINIATURA_LOCAL:
            return f"/media-thumb/{width}x{height}/{caminho_local}"
        return f"/media/{caminho_local}"
    if url_remota:
        return transformar_url_imagem(url_remota, width, height, quality)
    return _IMAGEM_EM_BRANCO


def url_absoluta(request, caminho: str) -> str:
    """https://dominio/caminho a partir do Request atual — usado em og:url, canonical,
    JSON-LD e sitemap.xml. Nunca hardcoda o host (o site roda hoje em IP:porta sem HTTPS,
    então derivar de request.base_url também significa que isso funciona sem alteração no
    dia em que o domínio+HTTPS entrar). Valor já absoluto (imagem remota do Supabase, data:
    URI do placeholder) passa direto."""
    if not caminho or caminho.startswith(("http://", "https://", "data:")):
        return caminho
    return str(request.base_url).rstrip("/") + "/" + caminho.lstrip("/")


def descricao_veiculo(veiculo, max_chars: int | None = None) -> str:
    """Descrição legível e ÚNICA por veículo, montada a partir de campos estruturados reais
    — substitui a exibição pública do `descricao` raspado do AutoCerto, que é o MESMO
    parágrafo em todo o estoque (conteúdo duplicado é ruim pra SEO). A coluna `descricao`
    continua intacta no banco pro bot do WhatsApp e pro admin; só a página pública para de
    mostrá-la. `max_chars` trunca na última palavra inteira (nunca no meio de uma palavra),
    pra caber no <meta name="description"> (~155 caracteres)."""
    titulo = " ".join(p for p in (veiculo.marca, veiculo.modelo, veiculo.versao) if p)
    specs = []
    if veiculo.ano:
        specs.append(str(veiculo.ano))
    if veiculo.quilometragem:
        specs.append(f"{veiculo.quilometragem:,}".replace(",", ".") + " km")
    for campo in (veiculo.cambio, veiculo.combustivel, veiculo.cor, veiculo.carroceria):
        if campo:
            specs.append(campo)

    texto = titulo
    if specs:
        texto += " — " + ", ".join(specs) + "."
    destaques = veiculo.destaques()
    if destaques:
        texto += " " + ", ".join(destaques) + "."

    if max_chars and len(texto) > max_chars:
        texto = texto[: max_chars - 1].rsplit(" ", 1)[0].rstrip(",.—") + "…"
    return texto


def json_ld_veiculo(veiculo, request) -> str:
    """JSON-LD schema.org/Vehicle — tipo genérico de propósito (estoque cobre carro, moto,
    caminhonete etc.). Omite campo ausente em vez de serializar `null`. O
    `.replace("</", "<\\/")` é defesa padrão pra JSON dentro de <script>: evita que algum
    texto com essa sequência feche a tag antes da hora."""
    imagem = imagem_src(veiculo.caminho_capa, veiculo.url_imagem_capa, 1000, 750)
    dados = {
        "@context": "https://schema.org",
        "@type": "Vehicle",
        "name": " ".join(p for p in (veiculo.marca, veiculo.modelo, veiculo.versao) if p),
        "brand": veiculo.marca,
        "model": veiculo.modelo,
        "vehicleModelDate": str(veiculo.ano) if veiculo.ano else None,
        "mileageFromOdometer": (
            {"@type": "QuantitativeValue", "value": veiculo.quilometragem, "unitCode": "KMT"}
            if veiculo.quilometragem
            else None
        ),
        "vehicleTransmission": veiculo.cambio,
        "fuelType": veiculo.combustivel,
        "color": veiculo.cor,
        "bodyType": veiculo.carroceria,
        "image": url_absoluta(request, imagem) if imagem and not imagem.startswith("data:") else None,
        "offers": (
            {
                "@type": "Offer",
                "price": veiculo.preco,
                "priceCurrency": "BRL",
                "availability": "https://schema.org/InStock",
                "itemCondition": "https://schema.org/UsedCondition",
                "url": url_absoluta(request, f"/veiculos/{veiculo.slug}"),
            }
            if veiculo.preco
            else None
        ),
    }
    dados = {k: v for k, v in dados.items() if v is not None}
    return json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")


def json_ld_loja(request) -> str:
    """JSON-LD schema.org/AutoDealer sitewide, lendo direto de dealership_config.py."""
    dados = {
        "@context": "https://schema.org",
        "@type": "AutoDealer",
        "name": DEALERSHIP_NAME,
        "telephone": DEALERSHIP_PHONE or None,
        "address": (
            {
                "@type": "PostalAddress",
                "streetAddress": DEALERSHIP_ADDRESS,
                "addressLocality": DEALERSHIP_CITY,
                "addressCountry": "BR",
            }
            if DEALERSHIP_ADDRESS
            else None
        ),
        "url": url_absoluta(request, "/"),
        "image": url_absoluta(request, "/static/logo.png"),
        "openingHours": DEALERSHIP_HOURS or None,
    }
    dados = {k: v for k, v in dados.items() if v is not None}
    return json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")


def registrar(templates) -> None:
    templates.env.filters["brl"] = brl
    templates.env.globals["imagem_src"] = imagem_src
    templates.env.globals["url_absoluta"] = url_absoluta
    templates.env.globals["descricao_veiculo"] = descricao_veiculo
    templates.env.globals["json_ld_veiculo"] = json_ld_veiculo
    templates.env.globals["json_ld_loja"] = json_ld_loja
