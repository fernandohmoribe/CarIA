from unittest.mock import patch

from fastapi.testclient import TestClient

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


def test_conversation_persists_across_page_reloads():
    client = _logged_in_client()
    assert client.get("/admin/testar-bot").status_code == 200

    with patch("admin.routes.get_ai_response") as mock_ai:
        mock_ai.return_value = ("Oi! Em que posso ajudar?", None, None)
        client.post("/admin/testar-bot/enviar", json={"message": "oi"})

    reloaded = client.get("/admin/testar-bot")
    assert "oi" in reloaded.text
    assert "Oi! Em que posso ajudar?" in reloaded.text
