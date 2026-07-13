"""
Resize + conversão pra WebP compartilhado entre o conector AutoCerto (baixa foto de URL
externa) e o upload manual de foto no admin (recebe bytes direto do formulário) — mesma
lógica, duas origens de bytes diferentes.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image


def redimensionar_e_salvar_webp(bytes_imagem: bytes, caminho_destino: Path, width: int = 1000, height: int = 750, quality: int = 78) -> None:
    img = Image.open(io.BytesIO(bytes_imagem)).convert("RGB")
    img.thumbnail((width, height))
    caminho_destino.parent.mkdir(parents=True, exist_ok=True)
    img.save(caminho_destino, format="WEBP", quality=quality)
