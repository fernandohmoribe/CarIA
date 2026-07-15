from fastapi.testclient import TestClient

from database import SessionLocal, obter_loja_padrao, obter_ou_criar_loja, obter_todos_leads
from main import app


def _loja_id():
    db = SessionLocal()
    loja = obter_loja_padrao(db) or obter_ou_criar_loja(
        db, nome="Loja Avaliacao Publico", tipo_conector="supabase", config_conector={}
    )
    loja_id = loja.id
    db.close()
    return loja_id


def test_avaliacao_page_loads():
    client = TestClient(app)
    resp = client.get("/avaliacao")
    assert resp.status_code == 200
    assert "Avalie seu carro na troca" in resp.text


def test_avaliacao_form_creates_lead_with_trade_in_description():
    loja_id = _loja_id()
    client = TestClient(app)
    resp = client.post(
        "/avaliacao",
        data={
            "nome": "Avaliacao Teste", "telefone": "44 97777-6666",
            "veiculo_troca_desc": "Fiat Uno 2015, 80 mil km, bom estado",
        },
    )
    assert resp.status_code == 200
    assert "Recebemos os dados do seu veículo" in resp.text

    db = SessionLocal()
    leads = obter_todos_leads(db, loja_id)
    db.close()
    match = [l for l in leads if l.numero_telefone == "44977776666"]
    assert len(match) == 1
    assert match[0].origem == "site"
    assert match[0].tem_troca is True
    assert match[0].veiculo_troca_desc == "Fiat Uno 2015, 80 mil km, bom estado"


def test_avaliacao_form_requires_nome_telefone_and_descricao():
    client = TestClient(app)
    resp = client.post("/avaliacao", data={"nome": "Sem Descricao", "telefone": "44988887777"})
    assert resp.status_code == 400
    assert "Preencha nome, telefone" in resp.text


def test_avaliacao_form_rate_limited_after_too_many_submissions():
    client = TestClient(app)
    last_status = None
    for i in range(7):
        resp = client.post(
            "/avaliacao",
            data={"nome": f"Spam {i}", "telefone": f"4499998{i:04d}", "veiculo_troca_desc": "Carro qualquer"},
        )
        last_status = resp.status_code
    assert last_status == 429


def test_avaliacao_submit_twice_same_phone_updates_same_lead():
    loja_id = _loja_id()
    client = TestClient(app)
    client.post(
        "/avaliacao",
        data={"nome": "Primeiro Nome", "telefone": "44 96666-5555", "veiculo_troca_desc": "Descrição inicial"},
    )
    client.post(
        "/avaliacao",
        data={"nome": "Nome Atualizado", "telefone": "(44) 966665555", "veiculo_troca_desc": "Descrição atualizada"},
    )

    db = SessionLocal()
    leads = obter_todos_leads(db, loja_id)
    db.close()
    match = [l for l in leads if l.numero_telefone == "44966665555"]
    assert len(match) == 1
    assert match[0].nome == "Nome Atualizado"
    assert match[0].veiculo_troca_desc == "Descrição atualizada"


def test_nav_includes_avaliacao_link():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.text.count('href="/avaliacao"') == 2  # desktop + mobile
