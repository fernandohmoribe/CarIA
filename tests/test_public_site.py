from fastapi.testclient import TestClient

from database import (
    AvaliacaoGoogle,
    PostInstagram,
    Novidade,
    SessionLocal,
    Veiculo,
    obter_loja_padrao,
    obter_ou_criar_loja,
)
from main import app


def _logged_in_client() -> TestClient:
    client = TestClient(app)
    client.post("/admin/login", data={"nome_usuario": "admin", "senha": "test-password"})
    return client


def _loja_id():
    db = SessionLocal()
    loja = obter_loja_padrao(db) or obter_ou_criar_loja(
        db, nome="Loja Site Publico", tipo_conector="supabase", config_conector={}
    )
    loja_id = loja.id
    db.close()
    return loja_id


def _make_veiculo(loja_id, slug, marca="Fiat", modelo="Mobi", **overrides):
    db = SessionLocal()
    data = dict(
        loja_id=loja_id, slug=slug, marca=marca, modelo=modelo, ano=2022, preco=90000.0,
        status="Disponivel", status_publicacao="Publicado", carroceria="Hatch", cambio="Manual", combustivel="Flex",
    )
    data.update(overrides)
    veiculo = Veiculo(**data)
    db.add(veiculo)
    db.commit()
    db.close()
    return slug


# ── Home ─────────────────────────────────────────────────────────────────
def test_home_shows_hero_and_consultor_form():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "INICIAR ATENDIMENTO" in resp.text


def test_home_hides_video_and_review_sections_when_empty():
    client = TestClient(app)
    resp = client.get("/")
    assert "Avaliações no Google" not in resp.text


def test_home_shows_video_section_when_instagram_post_visible():
    loja_id = _loja_id()
    db = SessionLocal()
    db.add(PostInstagram(
        loja_id=loja_id, id_midia="home-video-visivel", tipo_midia="VIDEO",
        url_midia="https://example.com/v.mp4", url_miniatura="https://example.com/thumb.jpg",
        link_permanente="https://instagram.com/p/xyz", visivel=True,
    ))
    db.commit()
    db.close()

    client = TestClient(app)
    resp = client.get("/")
    assert "thumb.jpg" in resp.text


def test_home_shows_reviews_when_present():
    loja_id = _loja_id()
    db = SessionLocal()
    db.add(AvaliacaoGoogle(
        loja_id=loja_id, nome_autor="Cliente Satisfeito", nota=5,
        texto="Ótimo atendimento!", tempo_relativo="há 1 semana",
    ))
    db.commit()
    db.close()

    client = TestClient(app)
    resp = client.get("/")
    assert "Avaliações no Google" in resp.text
    assert "Cliente Satisfeito" in resp.text


def test_consultor_form_creates_lead_and_redirects_to_whatsapp():
    client = TestClient(app)
    resp = client.post(
        "/consultor",
        data={"nome": "Consultor Teste", "telefone": "(44) 99999-1234", "carro": "SUV"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "wa.me" in resp.headers["location"]
    assert "Consultor" in resp.headers["location"] or "Consultor%20Teste" in resp.headers["location"]

    from database import obter_todos_leads
    db = SessionLocal()
    leads = obter_todos_leads(db, _loja_id())
    db.close()
    match = [l for l in leads if l.telefone == "(44) 99999-1234"]
    assert len(match) == 1
    assert match[0].origem == "site"
    assert match[0].veiculo_interesse == "SUV"


def test_consultor_form_requires_nome_and_telefone():
    client = TestClient(app)
    resp = client.post("/consultor", data={"nome": "", "telefone": ""})
    assert resp.status_code == 400


# ── Sobre Nós ────────────────────────────────────────────────────────────
def test_sobre_nos_page_loads():
    client = TestClient(app)
    resp = client.get("/sobre-nos")
    assert resp.status_code == 200
    assert "Sobre a" in resp.text


# ── Ícones (substituem 📞📍💬 por SVG, resto do emoji do site fica intocado) ─────
def test_home_whatsapp_button_uses_svg_not_emoji():
    client = TestClient(app)
    resp = client.get("/")
    assert "💬" not in resp.text
    assert "<svg" in resp.text
    assert "☰" in resp.text  # emoji fora do escopo continua intocado


def test_contato_page_uses_svg_icons_not_emoji():
    loja_id = _loja_id()
    client = TestClient(app)
    resp = client.get("/contato")
    assert "📞" not in resp.text
    assert "📍" not in resp.text
    assert "<svg" in resp.text


def test_sobre_nos_page_uses_svg_pin_not_emoji():
    client = TestClient(app)
    resp = client.get("/sobre-nos")
    assert "📍" not in resp.text


# ── Contato ──────────────────────────────────────────────────────────────
def test_contato_page_loads():
    client = TestClient(app)
    resp = client.get("/contato")
    assert resp.status_code == 200


def test_contato_form_creates_lead_without_vehicle():
    from database import obter_todos_leads

    loja_id = _loja_id()
    client = TestClient(app)
    resp = client.post(
        "/contato",
        data={"nome": "Contato Teste", "telefone": "44 98888-7777", "mensagem": "Quero saber mais"},
    )
    assert resp.status_code == 200
    assert "Recebemos sua mensagem" in resp.text

    db = SessionLocal()
    leads = obter_todos_leads(db, loja_id)
    db.close()
    match = [l for l in leads if l.numero_telefone == "44988887777"]
    assert len(match) == 1
    assert match[0].veiculo_interesse is None
    assert match[0].origem == "site"


# ── Novidades ────────────────────────────────────────────────────────────
def test_novidades_lists_only_published_posts():
    loja_id = _loja_id()
    db = SessionLocal()
    db.add(Novidade(loja_id=loja_id, titulo="Post Publicado Novidade", slug="post-publicado-novidade", publicado=True))
    db.add(Novidade(loja_id=loja_id, titulo="Post Rascunho Novidade", slug="post-rascunho-novidade", publicado=False))
    db.commit()
    db.close()

    client = TestClient(app)
    resp = client.get("/novidades")
    assert "Post Publicado Novidade" in resp.text
    assert "Post Rascunho Novidade" not in resp.text


def test_novidade_detail_200_published_404_draft():
    loja_id = _loja_id()
    db = SessionLocal()
    db.add(Novidade(loja_id=loja_id, titulo="Detalhe Publicado", slug="detalhe-publicado-novidade", publicado=True))
    db.add(Novidade(loja_id=loja_id, titulo="Detalhe Rascunho", slug="detalhe-rascunho-novidade", publicado=False))
    db.commit()
    db.close()

    client = TestClient(app)
    resp = client.get("/novidades/detalhe-publicado-novidade")
    assert resp.status_code == 200
    assert "Detalhe Publicado" in resp.text

    resp = client.get("/novidades/detalhe-rascunho-novidade")
    assert resp.status_code == 404


# ── Estoque com filtros ──────────────────────────────────────────────────
def test_catalog_filter_by_marca():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-marca-toyota", marca="Toyota", modelo="Corolla Filtro")
    _make_veiculo(loja_id, "filtro-marca-fiat", marca="Fiat", modelo="Mobi Filtro")

    client = TestClient(app)
    resp = client.get("/veiculos", params={"marca": "Toyota"})
    assert "Corolla Filtro" in resp.text
    assert "Mobi Filtro" not in resp.text


def test_catalog_filter_by_preco_range():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-preco-barato", modelo="Barato Filtro Preco", preco=50000.0)
    _make_veiculo(loja_id, "filtro-preco-caro", modelo="Caro Filtro Preco", preco=200000.0)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"preco_min": "100000"})
    assert "Caro Filtro Preco" in resp.text
    assert "Barato Filtro Preco" not in resp.text


def test_catalog_filter_by_carroceria_cambio_combustivel():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-suv-auto", modelo="SUV Filtro Combo", carroceria="SUV", cambio="Automático", combustivel="Diesel")
    _make_veiculo(loja_id, "filtro-hatch-manual", modelo="Hatch Filtro Combo", carroceria="Hatch", cambio="Manual", combustivel="Flex")

    client = TestClient(app)
    resp = client.get("/veiculos", params={"carroceria": "SUV", "cambio": "Automático", "combustivel": "Diesel"})
    assert "SUV Filtro Combo" in resp.text
    assert "Hatch Filtro Combo" not in resp.text


def test_catalog_filter_by_preco_max():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-preco-max-barato", modelo="Barato Filtro Preco Max", preco=50000.0)
    _make_veiculo(loja_id, "filtro-preco-max-caro", modelo="Caro Filtro Preco Max", preco=200000.0)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"preco_max": "100000"})
    assert "Barato Filtro Preco Max" in resp.text
    assert "Caro Filtro Preco Max" not in resp.text


def test_catalog_filter_by_preco_min_and_max_combined():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-preco-combo-baixo", modelo="Baixo Filtro Preco Combo", preco=30000.0)
    _make_veiculo(loja_id, "filtro-preco-combo-medio", modelo="Medio Filtro Preco Combo", preco=100000.0)
    _make_veiculo(loja_id, "filtro-preco-combo-alto", modelo="Alto Filtro Preco Combo", preco=300000.0)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"preco_min": "50000", "preco_max": "200000"})
    assert "Medio Filtro Preco Combo" in resp.text
    assert "Baixo Filtro Preco Combo" not in resp.text
    assert "Alto Filtro Preco Combo" not in resp.text


def test_catalog_form_submit_with_all_fields_blank_does_not_422():
    """Reproduz o bug real: o form manda preco_min/preco_max="" quando o usuário clica em
    "Filtrar" sem preencher nada — isso não pode virar erro de validação (422), tem que
    tratar como "sem filtro" e devolver a lista completa."""
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-form-vazio", modelo="Form Vazio Sem Filtro")

    client = TestClient(app)
    resp = client.get(
        "/veiculos",
        params={"marca": "", "preco_min": "", "preco_max": "", "carroceria": "", "cambio": "", "combustivel": ""},
    )
    assert resp.status_code == 200
    assert "Form Vazio Sem Filtro" in resp.text


def test_catalog_filter_with_some_fields_blank_and_others_filled():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-parcial-toyota", marca="Toyota", modelo="Corolla Filtro Parcial")
    _make_veiculo(loja_id, "filtro-parcial-fiat", marca="Fiat", modelo="Mobi Filtro Parcial")

    client = TestClient(app)
    resp = client.get(
        "/veiculos",
        params={"marca": "Toyota", "preco_min": "", "preco_max": "", "carroceria": "", "cambio": "", "combustivel": ""},
    )
    assert resp.status_code == 200
    assert "Corolla Filtro Parcial" in resp.text
    assert "Mobi Filtro Parcial" not in resp.text


def test_catalog_filter_with_non_numeric_preco_does_not_422():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-preco-invalido", modelo="Preco Invalido Sem Crash")

    client = TestClient(app)
    resp = client.get("/veiculos", params={"preco_min": "abc"})
    assert resp.status_code == 200
    assert "Preco Invalido Sem Crash" in resp.text


def test_catalog_no_results_message_when_filters_match_nothing():
    _loja_id()
    client = TestClient(app)
    resp = client.get("/veiculos", params={"marca": "MarcaQueNaoExisteNoBancoDeDados"})
    assert resp.status_code == 200
    assert "Nenhum veículo encontrado com esses filtros." in resp.text


def test_catalog_filter_dropdowns_populated_from_real_stock():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-opcoes-marca", marca="Chevrolet", modelo="Onix Filtro Opcoes", carroceria="Sedan", cambio="Automático", combustivel="Gasolina")

    client = TestClient(app)
    resp = client.get("/veiculos")
    assert resp.status_code == 200
    assert '<option value="Chevrolet"' in resp.text
    assert '<option value="Sedan"' in resp.text
    assert '<option value="Automático"' in resp.text
    assert '<option value="Gasolina"' in resp.text


def test_catalog_cor_dropdown_populated_from_real_stock():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-opcoes-cor", modelo="Onix Filtro Cor", cor="Prata")

    client = TestClient(app)
    resp = client.get("/veiculos")
    assert '<option value="Prata"' in resp.text


def test_catalog_filter_by_busca_matches_marca_modelo_versao_ano():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "busca-corolla-2020", marca="Toyota", modelo="Corolla Busca Composta", ano=2020)
    _make_veiculo(loja_id, "busca-corolla-2015", marca="Toyota", modelo="Corolla Busca Composta Antigo", ano=2015)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"busca": "corolla 2020"})
    assert "Corolla Busca Composta" in resp.text
    assert "Corolla Busca Composta Antigo" not in resp.text


def test_catalog_filter_by_busca_single_token_matches_any_field():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "busca-token-unico", marca="Renault", modelo="Sandero Busca Token Unico")
    _make_veiculo(loja_id, "busca-token-outro", marca="Fiat", modelo="Mobi Busca Token Outro")

    client = TestClient(app)
    resp = client.get("/veiculos", params={"busca": "renault"})
    assert "Sandero Busca Token Unico" in resp.text
    assert "Mobi Busca Token Outro" not in resp.text


def test_catalog_busca_with_no_matches_shows_empty_state():
    _loja_id()
    client = TestClient(app)
    resp = client.get("/veiculos", params={"busca": "termoquenaobatenenhumveiculo"})
    assert "Nenhum veículo encontrado com esses filtros." in resp.text


def test_catalog_filter_by_ano_range():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-ano-novo", modelo="Novo Filtro Ano", ano=2023)
    _make_veiculo(loja_id, "filtro-ano-velho", modelo="Velho Filtro Ano", ano=2010)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"ano_min": "2018"})
    assert "Novo Filtro Ano" in resp.text
    assert "Velho Filtro Ano" not in resp.text

    resp = client.get("/veiculos", params={"ano_max": "2018"})
    assert "Velho Filtro Ano" in resp.text
    assert "Novo Filtro Ano" not in resp.text


def test_catalog_filter_by_cor():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-cor-prata", modelo="Prata Filtro Cor", cor="Prata")
    _make_veiculo(loja_id, "filtro-cor-preto", modelo="Preto Filtro Cor", cor="Preto")

    client = TestClient(app)
    resp = client.get("/veiculos", params={"cor": "Prata"})
    assert "Prata Filtro Cor" in resp.text
    assert "Preto Filtro Cor" not in resp.text


def test_catalog_filter_by_km_max():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-km-baixo", modelo="Baixo Filtro Km", quilometragem=10000)
    _make_veiculo(loja_id, "filtro-km-alto", modelo="Alto Filtro Km", quilometragem=90000)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"km_max": "20000"})
    assert "Baixo Filtro Km" in resp.text
    assert "Alto Filtro Km" not in resp.text


def test_catalog_filter_with_non_numeric_ano_and_km_does_not_422():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "filtro-ano-km-invalido", modelo="Ano Km Invalido Sem Crash")

    client = TestClient(app)
    resp = client.get("/veiculos", params={"ano_min": "abc", "km_max": "xyz"})
    assert resp.status_code == 200
    assert "Ano Km Invalido Sem Crash" in resp.text


# ── Estoque: ordenação ───────────────────────────────────────────────────
def test_catalog_default_order_is_menor_preco_sem_parametro():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "ordem-default-caro", modelo="Caro Ordem Default", preco=200000.0)
    _make_veiculo(loja_id, "ordem-default-barato", modelo="Barato Ordem Default", preco=50000.0)

    client = TestClient(app)
    resp = client.get("/veiculos")
    assert resp.status_code == 200
    assert resp.text.index("Barato Ordem Default") < resp.text.index("Caro Ordem Default")


def test_catalog_ordena_por_maior_preco():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "ordem-desc-caro", modelo="Caro Ordem Desc", preco=200000.0)
    _make_veiculo(loja_id, "ordem-desc-barato", modelo="Barato Ordem Desc", preco=50000.0)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"ordenar": "preco_desc"})
    assert resp.status_code == 200
    assert resp.text.index("Caro Ordem Desc") < resp.text.index("Barato Ordem Desc")


def test_catalog_ordena_por_ano_mais_novo():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "ordem-ano-velho", modelo="Velho Ordem Ano", ano=2015)
    _make_veiculo(loja_id, "ordem-ano-novo", modelo="Novo Ordem Ano", ano=2024)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"ordenar": "ano_desc"})
    assert resp.status_code == 200
    assert resp.text.index("Novo Ordem Ano") < resp.text.index("Velho Ordem Ano")


def test_catalog_ordena_por_menor_km():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "ordem-km-alta", modelo="Alta Ordem Km", quilometragem=90000)
    _make_veiculo(loja_id, "ordem-km-baixa", modelo="Baixa Ordem Km", quilometragem=5000)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"ordenar": "km_asc"})
    assert resp.status_code == 200
    assert resp.text.index("Baixa Ordem Km") < resp.text.index("Alta Ordem Km")


def test_catalog_ordenar_desconhecido_cai_pro_default():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "ordem-invalida-caro", modelo="Caro Ordem Invalida", preco=200000.0)
    _make_veiculo(loja_id, "ordem-invalida-barato", modelo="Barato Ordem Invalida", preco=50000.0)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"ordenar": "valor-que-nao-existe"})
    assert resp.status_code == 200
    assert resp.text.index("Barato Ordem Invalida") < resp.text.index("Caro Ordem Invalida")


def test_catalog_ordenacao_combina_com_filtro_existente():
    loja_id = _loja_id()
    _make_veiculo(loja_id, "ordem-combo-fiat-caro", marca="Fiat", modelo="Caro Ordem Combo", preco=200000.0)
    _make_veiculo(loja_id, "ordem-combo-fiat-barato", marca="Fiat", modelo="Barato Ordem Combo", preco=50000.0)
    _make_veiculo(loja_id, "ordem-combo-toyota", marca="Toyota", modelo="Toyota Ordem Combo", preco=1000.0)

    client = TestClient(app)
    resp = client.get("/veiculos", params={"marca": "Fiat", "ordenar": "preco_desc"})
    assert resp.status_code == 200
    assert "Toyota Ordem Combo" not in resp.text
    assert resp.text.index("Caro Ordem Combo") < resp.text.index("Barato Ordem Combo")


# ── Admin: Novidades CRUD ────────────────────────────────────────────────
def test_admin_novidades_requires_login():
    client = TestClient(app)
    resp = client.get("/admin/novidades", follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_admin_can_create_edit_delete_novidade():
    client = _logged_in_client()

    resp = client.post(
        "/admin/novidades/novo", data={"titulo": "Chegou Novidade Admin", "publicado": "on"}, follow_redirects=False
    )
    assert resp.status_code in (302, 303)

    db = SessionLocal()
    from database import obter_todas_novidades
    posts = obter_todas_novidades(db, _loja_id())
    db.close()
    match = [p for p in posts if p.titulo == "Chegou Novidade Admin"]
    assert len(match) == 1
    slug = match[0].slug

    resp = client.get(f"/admin/novidades/{slug}/editar")
    assert resp.status_code == 200
    assert "Chegou Novidade Admin" in resp.text

    resp = client.post(
        f"/admin/novidades/{slug}/editar", data={"titulo": "Novidade Admin Editada", "publicado": "on"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    db = SessionLocal()
    posts = obter_todas_novidades(db, _loja_id())
    db.close()
    assert any(p.titulo == "Novidade Admin Editada" for p in posts)

    resp = client.post(f"/admin/novidades/{slug}/excluir", follow_redirects=False)
    assert resp.status_code in (302, 303)

    db = SessionLocal()
    posts = obter_todas_novidades(db, _loja_id())
    db.close()
    assert not any(p.slug == slug for p in posts)


# ── Admin: curadoria de Instagram ────────────────────────────────────────
def test_admin_instagram_toggle_visibility():
    loja_id = _loja_id()
    db = SessionLocal()
    post = PostInstagram(
        loja_id=loja_id, id_midia="admin-toggle-media", tipo_midia="VIDEO",
        url_midia="https://example.com/v2.mp4", link_permanente="https://instagram.com/p/abc", visivel=False,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    post_id = post.id
    db.close()

    client = _logged_in_client()
    resp = client.post(f"/admin/instagram/{post_id}/visibilidade", data={"visivel": "on"}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    from database import obter_posts_instagram_visiveis
    db = SessionLocal()
    visible = obter_posts_instagram_visiveis(db, loja_id)
    db.close()
    assert any(p.id == post_id for p in visible)
