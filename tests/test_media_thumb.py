import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import main


def _criar_imagem_teste(caminho: Path, tamanho=(1000, 750)):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", tamanho, color="red").save(caminho, format="JPEG")


def test_media_thumb_redimensiona_e_cacheia_em_disco(tmp_path):
    """Servir a foto original (~1000x750) num card pequeno de lista faz o navegador
    decodificar dezenas de imagens em tamanho de tela cheia à toa, travando o scroll do
    catálogo público — /media-thumb/ precisa gerar (e cachear) uma versão bem menor."""
    media_root = tmp_path / "media"
    _criar_imagem_teste(media_root / "vehicles" / "x" / "0.jpg")

    with patch.object(main, "MEDIA_ROOT", media_root), \
         patch.object(main, "MINIATURAS_ROOT", media_root / ".miniaturas"):
        client = TestClient(main.app)
        resp = client.get("/media-thumb/400x300/vehicles/x/0.jpg")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"

        destino = media_root / ".miniaturas" / "400x300" / "vehicles" / "x" / "0.jpg"
        assert destino.is_file()
        miniatura = Image.open(destino)
        assert miniatura.width <= 400 and miniatura.height <= 300


def test_media_thumb_rejeita_tamanho_fora_da_lista_permitida(tmp_path):
    """Tamanho vem fixo do código (TAMANHOS_MINIATURA_LOCAL), nunca do usuário — w/h
    arbitrário na URL não pode virar arquivo novo em cache sem limite."""
    media_root = tmp_path / "media"
    _criar_imagem_teste(media_root / "vehicles" / "x" / "0.jpg")

    with patch.object(main, "MEDIA_ROOT", media_root), \
         patch.object(main, "MINIATURAS_ROOT", media_root / ".miniaturas"):
        client = TestClient(main.app)
        resp = client.get("/media-thumb/999x999/vehicles/x/0.jpg")
        assert resp.status_code == 404


def test_media_thumb_bloqueia_path_traversal(tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True)
    fora_do_media = tmp_path / "segredo.jpg"
    _criar_imagem_teste(fora_do_media)

    with patch.object(main, "MEDIA_ROOT", media_root), \
         patch.object(main, "MINIATURAS_ROOT", media_root / ".miniaturas"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(main.media_thumb(400, 300, "../segredo.jpg"))
        assert exc.value.status_code == 404
