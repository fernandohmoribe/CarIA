"""
Filtro/globais de template compartilhados entre admin/routes.py e public/routes.py — cada
um tem sua própria instância de Jinja2Templates (Jinja não compartilha filtro entre
instâncias), mas a lógica em si mora só aqui.
"""

from __future__ import annotations


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


def registrar(templates) -> None:
    templates.env.filters["brl"] = brl
    templates.env.globals["imagem_src"] = imagem_src
