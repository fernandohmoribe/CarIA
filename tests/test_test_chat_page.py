import uuid as uuid_module
from unittest.mock import patch

from fastapi.testclient import TestClient

from database import Conversation, Lead, SessionLocal, get_default_dealership, get_or_create_dealership
from main import app


def _logged_in_client() -> TestClient:
    client = TestClient(app)
    client.post("/admin/login", data={"username": "admin", "password": "test-password"})
    return client


def test_test_chat_page_requires_login():
    client = TestClient(app)
    resp = client.get("/admin/testar-bot", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/login"


def test_test_chat_page_renders_when_logged_in():
    client = _logged_in_client()
    resp = client.get("/admin/testar-bot")
    assert resp.status_code == 200
    assert "Testar o bot" in resp.text


def test_send_requires_login():
    client = TestClient(app)
    resp = client.post("/admin/testar-bot/enviar", json={"message": "oi"})
    assert resp.status_code == 401


def test_send_calls_real_ai_pipeline_and_persists_conversation():
    client = _logged_in_client()
    client.get("/admin/testar-bot")  # gera o test_chat_phone na sessão

    with patch("admin.routes.get_ai_response") as mock_ai:
        mock_ai.return_value = ("Olá! Como posso ajudar?", None, None)
        resp = client.post("/admin/testar-bot/enviar", json={"message": "oi, quero um carro"})

    assert resp.status_code == 200
    assert resp.json()["reply"] == "Olá! Como posso ajudar?"
    mock_ai.assert_called_once()
    call_kwargs = mock_ai.call_args.kwargs
    assert call_kwargs["user_message"] == "oi, quero um carro"
    assert call_kwargs["phone"].startswith("teste-interno-")


def test_send_returns_photo_urls_when_ai_sends_photos():
    client = _logged_in_client()
    client.get("/admin/testar-bot")

    photos_payload = {
        "veiculo": "BMW R 18 PURE",
        "fotos": [
            {"local_path": "vehicles/bmw-r-18-pure/0.webp", "url": "https://example.com/0.jpg"},
            {"local_path": None, "url": "https://example.com/1.jpg"},
        ],
    }
    with patch("admin.routes.get_ai_response") as mock_ai:
        mock_ai.return_value = ("Te mandei as fotos! 📸", None, photos_payload)
        resp = client.post("/admin/testar-bot/enviar", json={"message": "manda fotos"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Te mandei as fotos! 📸"
    assert body["photos"] == [
        "/media/vehicles/bmw-r-18-pure/0.webp",  # tinha arquivo local, usa ele
        "https://example.com/1.jpg",  # sem local_path, cai pra URL remota
    ]


def test_send_returns_no_photos_when_ai_does_not_send_any():
    client = _logged_in_client()
    client.get("/admin/testar-bot")

    with patch("admin.routes.get_ai_response") as mock_ai:
        mock_ai.return_value = ("Claro, o preço é R$ 99.900.", None, None)
        resp = client.post("/admin/testar-bot/enviar", json={"message": "qual o preço?"})

    assert resp.json()["photos"] == []


def test_send_links_conversation_to_lead_when_lead_already_exists():
    """Regressão: a conversa do chat de teste precisa ficar com lead_id preenchido (igual o
    main.py já faz pro WhatsApp real), senão o histórico não aparece na tela do lead."""
    fixed_uuid = uuid_module.UUID("12345678-1234-5678-1234-567812345678")
    client = _logged_in_client()

    with patch("admin.routes.uuid.uuid4", return_value=fixed_uuid):
        client.get("/admin/testar-bot")
    phone = f"teste-interno-{fixed_uuid.hex[:12]}@admin"

    db = SessionLocal()
    dealership = get_default_dealership(db) or get_or_create_dealership(
        db, nome="Loja Teste Chat Link", connector_type="supabase", connector_config={}
    )
    lead = Lead(dealership_id=dealership.id, phone_number=phone, nome="Simulado", status="novo")
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()

    with patch("admin.routes.get_ai_response") as mock_ai:
        mock_ai.return_value = ("resposta", None, None)
        client.post("/admin/testar-bot/enviar", json={"message": "oi"})

    db = SessionLocal()
    conv = db.query(Conversation).filter(Conversation.phone_number == phone).first()
    db.close()
    assert conv is not None
    assert conv.lead_id == lead_id


def test_send_rejects_empty_message():
    client = _logged_in_client()
    client.get("/admin/testar-bot")

    with patch("admin.routes.get_ai_response") as mock_ai:
        resp = client.post("/admin/testar-bot/enviar", json={"message": "   "})

    assert resp.status_code == 400
    mock_ai.assert_not_called()


def test_reset_closes_conversation_and_starts_fresh():
    client = _logged_in_client()
    client.get("/admin/testar-bot")

    with patch("admin.routes.get_ai_response") as mock_ai:
        mock_ai.return_value = ("resposta", None, None)
        client.post("/admin/testar-bot/enviar", json={"message": "oi"})

    resp = client.post("/admin/testar-bot/reiniciar", follow_redirects=False)
    assert resp.status_code == 302

    reloaded = client.get("/admin/testar-bot")
    assert "Sem mensagens ainda" in reloaded.text


def test_reset_generates_new_test_phone_so_leads_dont_collide():
    """Regressão: reiniciar não pode reaproveitar o mesmo telefone — senão testar como "João"
    depois de "Fernando" atualiza o lead antigo em vez de criar um novo (era o bug relatado)."""
    client = _logged_in_client()

    first_uuid = uuid_module.UUID("11111111-1111-1111-1111-111111111111")
    with patch("admin.routes.uuid.uuid4", return_value=first_uuid):
        client.get("/admin/testar-bot")
    first_phone = f"teste-interno-{first_uuid.hex[:12]}@admin"

    with patch("admin.routes.get_ai_response") as mock_ai:
        mock_ai.return_value = ("resposta", None, None)
        client.post("/admin/testar-bot/enviar", json={"message": "oi, sou o Fernando"})

    second_uuid = uuid_module.UUID("22222222-2222-2222-2222-222222222222")
    with patch("admin.routes.uuid.uuid4", return_value=second_uuid):
        client.post("/admin/testar-bot/reiniciar")
    second_phone = f"teste-interno-{second_uuid.hex[:12]}@admin"

    assert second_phone != first_phone

    with patch("admin.routes.get_ai_response") as mock_ai:
        mock_ai.return_value = ("resposta 2", None, None)
        client.post("/admin/testar-bot/enviar", json={"message": "oi, sou o João"})

    db = SessionLocal()
    conv_first = db.query(Conversation).filter(Conversation.phone_number == first_phone).first()
    conv_second = db.query(Conversation).filter(Conversation.phone_number == second_phone).first()
    db.close()
    assert conv_first is not None
    assert conv_second is not None


def test_conversation_persists_across_page_reloads():
    client = _logged_in_client()
    assert client.get("/admin/testar-bot").status_code == 200

    with patch("admin.routes.get_ai_response") as mock_ai:
        mock_ai.return_value = ("Oi! Em que posso ajudar?", None, None)
        client.post("/admin/testar-bot/enviar", json={"message": "oi"})

    reloaded = client.get("/admin/testar-bot")
    assert "oi" in reloaded.text
    assert "Oi! Em que posso ajudar?" in reloaded.text
