from unittest.mock import MagicMock

import httpx
import pytest

from database import (
    SessionLocal,
    get_default_dealership,
    get_google_reviews,
    get_or_create_dealership,
    get_visible_instagram_posts,
)


def _dealership_id():
    db = SessionLocal()
    dealership = get_default_dealership(db) or get_or_create_dealership(
        db, nome="Loja Sync Externo", connector_type="supabase", connector_config={}
    )
    dealership_id = dealership.id
    db.close()
    return dealership_id


# ── Instagram ────────────────────────────────────────────────────────────
def test_sync_instagram_noop_without_credentials(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", raising=False)

    import sync_instagram

    assert sync_instagram.run_sync() == 0


def test_sync_instagram_upserts_from_mocked_api(monkeypatch):
    _dealership_id()
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

    total = sync_instagram.run_sync()
    assert total == 1

    db = SessionLocal()
    from database import get_all_instagram_posts
    posts = get_all_instagram_posts(db, _dealership_id())
    db.close()
    match = [p for p in posts if p.media_id == "media-fixture-1"]
    assert len(match) == 1
    assert match[0].visivel is False  # precisa de curadoria manual antes de aparecer no site


# ── Google Reviews ───────────────────────────────────────────────────────
def test_sync_google_reviews_noop_without_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_PLACE_ID", raising=False)

    import sync_google_reviews

    assert sync_google_reviews.run_sync() == 0


def test_sync_google_reviews_replaces_cache_from_mocked_api(monkeypatch):
    dealership_id = _dealership_id()
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

    total = sync_google_reviews.run_sync()
    assert total == 1

    db = SessionLocal()
    reviews = get_google_reviews(db, dealership_id)
    db.close()
    assert len(reviews) == 1
    assert reviews[0].author_name == "Cliente Fixture"

    # Roda de novo com uma lista vazia — precisa substituir o cache, não acumular.
    fake_response.json.return_value = {"result": {"reviews": []}}
    sync_google_reviews.run_sync()

    db = SessionLocal()
    reviews = get_google_reviews(db, dealership_id)
    db.close()
    assert len(reviews) == 0
