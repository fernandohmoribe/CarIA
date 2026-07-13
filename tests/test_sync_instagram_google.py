from unittest.mock import MagicMock

import httpx
import pytest

from database import (
    SessionLocal,
    obter_avaliacoes_google,
    obter_loja_padrao,
    obter_ou_criar_loja,
    obter_posts_instagram_visiveis,
)


def _loja_id():
    db = SessionLocal()
    loja = obter_loja_padrao(db) or obter_ou_criar_loja(
        db, nome="Loja Sync Externo", tipo_conector="supabase", config_conector={}
    )
    loja_id = loja.id
    db.close()
    return loja_id


# ── Instagram ────────────────────────────────────────────────────────────
def test_sync_instagram_noop_without_credentials(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", raising=False)

    import sync_instagram

    assert sync_instagram.rodar_sincronizacao() == 0


def test_sync_instagram_upserts_from_mocked_api(monkeypatch):
    _loja_id()
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "fake-token")
    monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "fake-ig-user-id")

    fake_response = MagicMock(spec=httpx.Response)
    fake_response.json.return_value = {
        "data": [
            {
                "id": "media-fixture-1",
                "caption": "Depoimento de cliente feliz",
                "media_type": "VIDEO",
                "media_url": "https://example.com/media1.mp4",
                "thumbnail_url": "https://example.com/thumb1.jpg",
                "permalink": "https://instagram.com/p/fixture1",
                "timestamp": "2026-01-15T12:00:00+0000",
            }
        ]
    }
    fake_response.raise_for_status.return_value = None
    monkeypatch.setattr(httpx, "get", lambda *a, **k: fake_response)

    import sync_instagram

    total = sync_instagram.rodar_sincronizacao()
    assert total == 1

    db = SessionLocal()
    from database import obter_todos_posts_instagram
    posts = obter_todos_posts_instagram(db, _loja_id())
    db.close()
    match = [p for p in posts if p.id_midia == "media-fixture-1"]
    assert len(match) == 1
    assert match[0].visivel is False  # precisa de curadoria manual antes de aparecer no site


# ── Google Reviews ───────────────────────────────────────────────────────
def test_sync_google_reviews_noop_without_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_PLACE_ID", raising=False)

    import sync_google_reviews

    assert sync_google_reviews.rodar_sincronizacao() == 0


def test_sync_google_reviews_replaces_cache_from_mocked_api(monkeypatch):
    loja_id = _loja_id()
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "fake-key")
    monkeypatch.setenv("GOOGLE_PLACE_ID", "fake-place-id")

    fake_response = MagicMock(spec=httpx.Response)
    fake_response.json.return_value = {
        "result": {
            "rating": 4.7,
            "reviews": [
                {
                    "author_name": "Cliente Fixture",
                    "profile_photo_url": "https://example.com/avatar.jpg",
                    "rating": 5,
                    "text": "Excelente atendimento!",
                    "relative_time_description": "há 2 semanas",
                }
            ],
        }
    }
    fake_response.raise_for_status.return_value = None
    monkeypatch.setattr(httpx, "get", lambda *a, **k: fake_response)

    import sync_google_reviews

    total = sync_google_reviews.rodar_sincronizacao()
    assert total == 1

    db = SessionLocal()
    avaliacoes = obter_avaliacoes_google(db, loja_id)
    db.close()
    assert len(avaliacoes) == 1
    assert avaliacoes[0].nome_autor == "Cliente Fixture"

    # Roda de novo com uma lista vazia — precisa substituir o cache, não acumular.
    fake_response.json.return_value = {"result": {"reviews": []}}
    sync_google_reviews.rodar_sincronizacao()

    db = SessionLocal()
    avaliacoes = obter_avaliacoes_google(db, loja_id)
    db.close()
    assert len(avaliacoes) == 0
