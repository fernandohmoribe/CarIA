"""
Geração de slug legível e único pra veículo cadastrado manualmente (sem conector externo
fornecendo um slug pronto). Sem dependência nova — mesma disciplina que
connectors/autocerto_connector.py já usa (texto legível + sufixo que garante unicidade),
só que aqui não existe um id_externo externo pra usar como sufixo, então a unicidade é
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


def gerar_slug_unico(
    db,
    loja_id: int,
    marca: str,
    modelo: str,
    versao: str | None = None,
    ano: int | None = None,
    max_tentativas: int = 50,
) -> str:
    """Monta um slug a partir de marca/modelo/versão/ano e garante que não colide com nenhum
    outro veículo da mesma loja — sufixo -2, -3... e, se esgotar as tentativas, um sufixo
    aleatório curto como último recurso."""
    from database import obter_veiculo_por_slug

    partes = [p for p in (marca, modelo, versao, str(ano) if ano else None) if p]
    base = slugify(" ".join(partes)) or "veiculo"

    candidato = base
    if obter_veiculo_por_slug(db, loja_id, candidato) is None:
        return candidato

    for tentativa in range(2, max_tentativas + 2):
        candidato = f"{base}-{tentativa}"
        if obter_veiculo_por_slug(db, loja_id, candidato) is None:
            return candidato

    return f"{base}-{uuid.uuid4().hex[:6]}"


def gerar_slug_unico_novidade(db, loja_id: int, titulo: str, max_tentativas: int = 50) -> str:
    """Mesma lógica de gerar_slug_unico, mas pra Novidade (baseado só no título)."""
    from database import obter_novidade_por_slug

    base = slugify(titulo) or "novidade"

    candidato = base
    if obter_novidade_por_slug(db, loja_id, candidato, apenas_publicada=False) is None:
        return candidato

    for tentativa in range(2, max_tentativas + 2):
        candidato = f"{base}-{tentativa}"
        if obter_novidade_por_slug(db, loja_id, candidato, apenas_publicada=False) is None:
            return candidato

    return f"{base}-{uuid.uuid4().hex[:6]}"
