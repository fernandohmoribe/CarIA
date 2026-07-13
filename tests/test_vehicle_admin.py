from pathlib import Path

from fastapi.testclient import TestClient

from database import SessionLocal, Veiculo, obter_loja_padrao, obter_ou_criar_loja
from main import app


def _logged_in_client() -> TestClient:
    client = TestClient(app)
    client.post("/admin/login", data={"nome_usuario": "admin", "senha": "test-password"})
    return client


def _loja_id():
    db = SessionLocal()
    loja = obter_loja_padrao(db) or obter_ou_criar_loja(
        db, nome="Loja Vehicle Admin", tipo_conector="supabase", config_conector={}
    )
    loja_id = loja.id
    db.close()
    return loja_id


def _tiny_jpeg_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format="JPEG")
    return buf.getvalue()


def test_unauthenticated_cannot_access_create_edit_delete_routes():
    _loja_id()
    client = TestClient(app)

    resp = client.get("/admin/veiculos/novo", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/admin/login"

    resp = client.post("/admin/veiculos/novo", data={"marca": "Fiat", "modelo": "Uno"}, follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/admin/login"

    resp = client.get("/admin/veiculos/algum-slug/editar", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/admin/login"

    resp = client.post("/admin/veiculos/algum-slug/editar", data={"marca": "Fiat"}, follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/admin/login"

    resp = client.post("/admin/veiculos/algum-slug/excluir", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/admin/login"


def test_create_vehicle_generates_slug_and_persists_row():
    _loja_id()
    client = _logged_in_client()

    resp = client.post(
        "/admin/veiculos/novo",
        data={
            "marca": "Fiat", "modelo": "Argo Teste Admin", "versao": "Drive", "ano": "2023",
            "preco": "80000", "status": "Disponivel", "status_publicacao": "Publicado",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.modelo == "Argo Teste Admin").first()
    assert veiculo is not None
    assert veiculo.slug == "fiat-argo-teste-admin-drive-2023"
    assert veiculo.preco == 80000.0
    db.close()


def test_create_vehicle_with_photo_saves_webp_and_local_path():
    loja_id = _loja_id()
    client = _logged_in_client()

    photo = _tiny_jpeg_bytes()
    resp = client.post(
        "/admin/veiculos/novo",
        data={"marca": "Renault", "modelo": "Kwid Foto Teste", "ano": "2023"},
        files={"photos": ("foto.jpg", photo, "image/jpeg")},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.modelo == "Kwid Foto Teste").first()
    assert veiculo is not None
    assert len(veiculo.imagens) == 1
    img = veiculo.imagens[0]
    assert img.caminho_local == f"vehicles/{veiculo.slug}/0.webp"
    assert img.eh_capa is True
    media_root = Path(__file__).parent.parent / "media"
    assert (media_root / img.caminho_local).exists()
    db.close()


def test_edit_vehicle_updates_in_place_not_duplicate():
    _loja_id()
    client = _logged_in_client()

    client.post(
        "/admin/veiculos/novo",
        data={"marca": "Chevrolet", "modelo": "Onix Edicao Teste", "ano": "2020"},
        follow_redirects=False,
    )
    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.modelo == "Onix Edicao Teste").first()
    original_slug = veiculo.slug
    db.close()

    client.post(
        f"/admin/veiculos/{original_slug}/editar",
        data={"marca": "Chevrolet", "modelo": "Onix Editado", "ano": "2021", "preco": "70000"},
        follow_redirects=False,
    )

    db = SessionLocal()
    matches = db.query(Veiculo).filter(Veiculo.slug == original_slug).all()
    assert len(matches) == 1
    assert matches[0].modelo == "Onix Editado"
    assert matches[0].preco == 70000.0
    db.close()


def test_edit_vehicle_without_new_photos_keeps_existing_images():
    _loja_id()
    client = _logged_in_client()

    photo = _tiny_jpeg_bytes()
    client.post(
        "/admin/veiculos/novo",
        data={"marca": "Hyundai", "modelo": "HB20 Foto Preserva Teste", "ano": "2022"},
        files={"photos": ("foto.jpg", photo, "image/jpeg")},
        follow_redirects=False,
    )
    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.modelo == "HB20 Foto Preserva Teste").first()
    slug = veiculo.slug
    db.close()

    client.post(
        f"/admin/veiculos/{slug}/editar",
        data={"marca": "Hyundai", "modelo": "HB20 Foto Preserva Teste", "ano": "2022", "preco": "60000"},
        follow_redirects=False,
    )

    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.slug == slug).first()
    assert len(veiculo.imagens) == 1
    db.close()


def test_two_vehicles_same_brand_model_get_distinct_slugs():
    _loja_id()
    client = _logged_in_client()

    for _ in range(2):
        client.post(
            "/admin/veiculos/novo",
            data={"marca": "Jeep", "modelo": "Renegade Slug Teste", "versao": "Sport", "ano": "2021"},
            follow_redirects=False,
        )

    db = SessionLocal()
    veiculos = db.query(Veiculo).filter(Veiculo.modelo == "Renegade Slug Teste").all()
    assert len(veiculos) == 2
    slugs = {v.slug for v in veiculos}
    assert len(slugs) == 2
    db.close()


def test_delete_vehicle_removes_row_and_media_files():
    _loja_id()
    client = _logged_in_client()

    photo = _tiny_jpeg_bytes()
    client.post(
        "/admin/veiculos/novo",
        data={"marca": "Nissan", "modelo": "Kicks Exclusao Teste", "ano": "2022"},
        files={"photos": ("foto.jpg", photo, "image/jpeg")},
        follow_redirects=False,
    )
    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.modelo == "Kicks Exclusao Teste").first()
    slug = veiculo.slug
    veiculo_id = veiculo.id
    db.close()

    media_root = Path(__file__).parent.parent / "media"
    veiculo_dir = media_root / "vehicles" / slug
    assert veiculo_dir.exists()

    resp = client.post(f"/admin/veiculos/{slug}/excluir", follow_redirects=False)
    assert resp.status_code == 302

    db = SessionLocal()
    assert db.query(Veiculo).filter(Veiculo.id == veiculo_id).first() is None
    db.close()
    assert not veiculo_dir.exists()


def test_create_vehicle_with_checkboxes_and_checklist_persists_correctly():
    _loja_id()
    client = _logged_in_client()

    client.post(
        "/admin/veiculos/novo",
        data={
            "marca": "Toyota", "modelo": "Hilux Checkbox Teste", "ano": "2023", "preco": "250000",
            "quilometragem": "5000", "carroceria": "Picape", "cambio": "Automático", "combustivel": "Diesel", "cor": "Prata",
            "cidade": "São Paulo - SP", "final_placa": "3",
            "blindado": "on", "aceita_troca": "on", "garantia_fabrica": "on",
            "destaques": ["Airbag", "GPS"],
            "outros_destaques": "Item customizado",
        },
        follow_redirects=False,
    )

    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.modelo == "Hilux Checkbox Teste").first()
    assert veiculo.blindado is True
    assert veiculo.aceita_troca is True
    assert veiculo.garantia_fabrica is True
    assert veiculo.unico_dono is False  # não marcado, tem que ficar False, não None
    assert veiculo.cidade == "São Paulo - SP"
    assert veiculo.final_placa == "3"
    assert set(veiculo.destaques()) == {"Airbag", "GPS", "Item customizado"}
    db.close()


def test_unchecking_box_on_edit_saves_as_false():
    _loja_id()
    client = _logged_in_client()

    client.post(
        "/admin/veiculos/novo",
        data={"marca": "Fiat", "modelo": "Toro Desmarcar Teste", "ano": "2022", "blindado": "on"},
        follow_redirects=False,
    )
    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.modelo == "Toro Desmarcar Teste").first()
    slug = veiculo.slug
    assert veiculo.blindado is True
    db.close()

    client.post(
        f"/admin/veiculos/{slug}/editar",
        data={"marca": "Fiat", "modelo": "Toro Desmarcar Teste", "ano": "2022"},  # blindado ausente = desmarcado
        follow_redirects=False,
    )

    db = SessionLocal()
    veiculo = db.query(Veiculo).filter(Veiculo.slug == slug).first()
    assert veiculo.blindado is False
    db.close()
