from pathlib import Path

from fastapi.testclient import TestClient

from database import SessionLocal, Vehicle, get_default_dealership, get_or_create_dealership
from main import app


def _logged_in_client() -> TestClient:
    client = TestClient(app)
    client.post("/admin/login", data={"username": "admin", "password": "test-password"})
    return client


def _dealership_id():
    db = SessionLocal()
    dealership = get_default_dealership(db) or get_or_create_dealership(
        db, nome="Loja Vehicle Admin", connector_type="supabase", connector_config={}
    )
    dealership_id = dealership.id
    db.close()
    return dealership_id


def _tiny_jpeg_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format="JPEG")
    return buf.getvalue()


def test_unauthenticated_cannot_access_create_edit_delete_routes():
    _dealership_id()
    client = TestClient(app)

    resp = client.get("/admin/vehicles/novo", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/admin/login"

    resp = client.post("/admin/vehicles/novo", data={"brand": "Fiat", "model": "Uno"}, follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/admin/login"

    resp = client.get("/admin/vehicles/algum-slug/editar", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/admin/login"

    resp = client.post("/admin/vehicles/algum-slug/editar", data={"brand": "Fiat"}, follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/admin/login"

    resp = client.post("/admin/vehicles/algum-slug/excluir", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/admin/login"


def test_create_vehicle_generates_slug_and_persists_row():
    _dealership_id()
    client = _logged_in_client()

    resp = client.post(
        "/admin/vehicles/novo",
        data={
            "brand": "Fiat", "model": "Argo Teste Admin", "version": "Drive", "year": "2023",
            "price": "80000", "status": "Disponivel", "publication_status": "Publicado",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    db = SessionLocal()
    vehicle = db.query(Vehicle).filter(Vehicle.model == "Argo Teste Admin").first()
    assert vehicle is not None
    assert vehicle.slug == "fiat-argo-teste-admin-drive-2023"
    assert vehicle.price == 80000.0
    db.close()


def test_create_vehicle_with_photo_saves_webp_and_local_path():
    dealership_id = _dealership_id()
    client = _logged_in_client()

    photo = _tiny_jpeg_bytes()
    resp = client.post(
        "/admin/vehicles/novo",
        data={"brand": "Renault", "model": "Kwid Foto Teste", "year": "2023"},
        files={"photos": ("foto.jpg", photo, "image/jpeg")},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    db = SessionLocal()
    vehicle = db.query(Vehicle).filter(Vehicle.model == "Kwid Foto Teste").first()
    assert vehicle is not None
    assert len(vehicle.images) == 1
    img = vehicle.images[0]
    assert img.local_path == f"vehicles/{vehicle.slug}/0.webp"
    assert img.is_cover is True
    media_root = Path(__file__).parent.parent / "media"
    assert (media_root / img.local_path).exists()
    db.close()


def test_edit_vehicle_updates_in_place_not_duplicate():
    _dealership_id()
    client = _logged_in_client()

    client.post(
        "/admin/vehicles/novo",
        data={"brand": "Chevrolet", "model": "Onix Edicao Teste", "year": "2020"},
        follow_redirects=False,
    )
    db = SessionLocal()
    vehicle = db.query(Vehicle).filter(Vehicle.model == "Onix Edicao Teste").first()
    original_slug = vehicle.slug
    db.close()

    client.post(
        f"/admin/vehicles/{original_slug}/editar",
        data={"brand": "Chevrolet", "model": "Onix Editado", "year": "2021", "price": "70000"},
        follow_redirects=False,
    )

    db = SessionLocal()
    matches = db.query(Vehicle).filter(Vehicle.slug == original_slug).all()
    assert len(matches) == 1
    assert matches[0].model == "Onix Editado"
    assert matches[0].price == 70000.0
    db.close()


def test_edit_vehicle_without_new_photos_keeps_existing_images():
    _dealership_id()
    client = _logged_in_client()

    photo = _tiny_jpeg_bytes()
    client.post(
        "/admin/vehicles/novo",
        data={"brand": "Hyundai", "model": "HB20 Foto Preserva Teste", "year": "2022"},
        files={"photos": ("foto.jpg", photo, "image/jpeg")},
        follow_redirects=False,
    )
    db = SessionLocal()
    vehicle = db.query(Vehicle).filter(Vehicle.model == "HB20 Foto Preserva Teste").first()
    slug = vehicle.slug
    db.close()

    client.post(
        f"/admin/vehicles/{slug}/editar",
        data={"brand": "Hyundai", "model": "HB20 Foto Preserva Teste", "year": "2022", "price": "60000"},
        follow_redirects=False,
    )

    db = SessionLocal()
    vehicle = db.query(Vehicle).filter(Vehicle.slug == slug).first()
    assert len(vehicle.images) == 1
    db.close()


def test_two_vehicles_same_brand_model_get_distinct_slugs():
    _dealership_id()
    client = _logged_in_client()

    for _ in range(2):
        client.post(
            "/admin/vehicles/novo",
            data={"brand": "Jeep", "model": "Renegade Slug Teste", "version": "Sport", "year": "2021"},
            follow_redirects=False,
        )

    db = SessionLocal()
    vehicles = db.query(Vehicle).filter(Vehicle.model == "Renegade Slug Teste").all()
    assert len(vehicles) == 2
    slugs = {v.slug for v in vehicles}
    assert len(slugs) == 2
    db.close()


def test_delete_vehicle_removes_row_and_media_files():
    _dealership_id()
    client = _logged_in_client()

    photo = _tiny_jpeg_bytes()
    client.post(
        "/admin/vehicles/novo",
        data={"brand": "Nissan", "model": "Kicks Exclusao Teste", "year": "2022"},
        files={"photos": ("foto.jpg", photo, "image/jpeg")},
        follow_redirects=False,
    )
    db = SessionLocal()
    vehicle = db.query(Vehicle).filter(Vehicle.model == "Kicks Exclusao Teste").first()
    slug = vehicle.slug
    vehicle_id = vehicle.id
    db.close()

    media_root = Path(__file__).parent.parent / "media"
    vehicle_dir = media_root / "vehicles" / slug
    assert vehicle_dir.exists()

    resp = client.post(f"/admin/vehicles/{slug}/excluir", follow_redirects=False)
    assert resp.status_code == 302

    db = SessionLocal()
    assert db.query(Vehicle).filter(Vehicle.id == vehicle_id).first() is None
    db.close()
    assert not vehicle_dir.exists()
