"""
Resize + conversão pra WebP compartilhado entre o conector AutoCerto (baixa foto de URL
externa) e o upload manual de foto no admin (recebe bytes direto do formulário) — mesma
lógica, duas origens de bytes diferentes.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image


def resize_and_save_webp(image_bytes: bytes, dest_path: Path, width: int = 1000, height: int = 750, quality: int = 78) -> None:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((width, height))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest_path, format="WEBP", quality=quality)
