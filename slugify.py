"""
Geração de slug legível e único pra veículo cadastrado manualmente (sem conector externo
fornecendo um slug pronto). Sem dependência nova — mesma disciplina que
connectors/autocerto_connector.py já usa (texto legível + sufixo que garante unicidade),
só que aqui não existe um external_id externo pra usar como sufixo, então a unicidade é
verificada contra o banco.
"""

from __future__ import annotations

import re
import unicodedata
import uuid


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return text


def generate_unique_slug(
    db,
    dealership_id: int,
    brand: str,
    model: str,
    version: str | None = None,
    year: int | None = None,
    max_attempts: int = 50,
) -> str:
    """Monta um slug a partir de marca/modelo/versão/ano e garante que não colide com nenhum
    outro veículo da mesma loja — sufixo -2, -3... e, se esgotar as tentativas, um sufixo
    aleatório curto como último recurso."""
    from database import get_vehicle_by_slug

    parts = [p for p in (brand, model, version, str(year) if year else None) if p]
    base = slugify(" ".join(parts)) or "veiculo"

    candidate = base
    if get_vehicle_by_slug(db, dealership_id, candidate) is None:
        return candidate

    for attempt in range(2, max_attempts + 2):
        candidate = f"{base}-{attempt}"
        if get_vehicle_by_slug(db, dealership_id, candidate) is None:
            return candidate

    return f"{base}-{uuid.uuid4().hex[:6]}"


def generate_unique_news_slug(db, dealership_id: int, titulo: str, max_attempts: int = 50) -> str:
    """Mesma lógica de generate_unique_slug, mas pra NewsPost (baseado só no título)."""
    from database import get_news_post_by_slug

    base = slugify(titulo) or "novidade"

    candidate = base
    if get_news_post_by_slug(db, dealership_id, candidate, only_published=False) is None:
        return candidate

    for attempt in range(2, max_attempts + 2):
        candidate = f"{base}-{attempt}"
        if get_news_post_by_slug(db, dealership_id, candidate, only_published=False) is None:
            return candidate

    return f"{base}-{uuid.uuid4().hex[:6]}"
