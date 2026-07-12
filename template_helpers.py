"""
Filtro/globais de template compartilhados entre admin/routes.py e public/routes.py — cada
um tem sua própria instância de Jinja2Templates (Jinja não compartilha filtro entre
instâncias), mas a lógica em si mora só aqui.
"""

from __future__ import annotations


def transform_image_url(url: str, width: int, height: int, quality: int = 65) -> str:
    """Usa a API de transformação de imagem do Supabase pra servir thumbnails leves."""
    marker = "/storage/v1/object/public/"
    idx = url.find(marker) if url else -1
    if idx == -1:
        return url
    base = url[:idx]
    rest = url[idx + len(marker):]
    return f"{base}/storage/v1/render/image/public/{rest}?width={width}&height={height}&resize=cover&quality={quality}"


def brl(value) -> str:
    if value is None:
        return "0,00"
    formatted = f"{value:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def img_src(local_path: str, remote_url: str, width: int, height: int, quality: int = 65) -> str:
    """Prefere a foto já baixada em media/ — só cai pro Supabase se ainda não tiver sido baixada."""
    if local_path:
        return f"/media/{local_path}"
    return transform_image_url(remote_url, width, height, quality)


def register(templates) -> None:
    templates.env.filters["brl"] = brl
    templates.env.globals["img_src"] = img_src
