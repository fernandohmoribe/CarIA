from database import ImagemVeiculo, SessionLocal, Veiculo, obter_ou_criar_loja

import inventory


def _make_loja(db, nome="Loja Inventory"):
    return obter_ou_criar_loja(db, nome=nome, tipo_conector="supabase", config_conector={})


def _make_veiculo(db, loja_id, **kwargs):
    defaults = dict(
        slug=f"v-{kwargs.get('marca', 'x')}-{kwargs.get('modelo', 'x')}".lower().replace(" ", "-"),
        status="Disponivel",
        status_publicacao="Publicado",
        preco=100000.0,
    )
    defaults.update(kwargs)
    veiculo = Veiculo(loja_id=loja_id, **defaults)
    db.add(veiculo)
    db.commit()
    return veiculo


def test_buscar_veiculos_termo_com_hifen_na_marca_encontra_veiculo():
    """Reproduz o bug real: a IA busca com "Mercedes-Benz A200" (hífen) mas o banco guarda a
    marca como "Mercedes Benz" (espaço) — o token hifenizado não pode zerar o resultado."""
    db = SessionLocal()
    loja = _make_loja(db)
    _make_veiculo(db, loja.id, marca="Mercedes Benz", modelo="A200", versao="SD HI")

    resultado = inventory.buscar_veiculos(loja_id=loja.id, termo="Mercedes-Benz A200")

    assert isinstance(resultado, list)
    assert len(resultado) == 1


def test_buscar_veiculos_termo_com_ano_encontra_veiculo():
    """Reproduz bug real visto em produção: cliente perguntou por "Cruze 1.4 Turbo LT 2018" e
    o bot disse que não tinha o carro nem fotos — mas o veículo existia no estoque. Causa: ano
    não aparece em marca/modelo/versão, e como cada palavra do termo precisa bater em algum
    desses três campos (AND), incluir o ano no termo zerava o resultado sempre."""
    db = SessionLocal()
    loja = _make_loja(db, "Loja Busca Com Ano")
    _make_veiculo(db, loja.id, marca="Chevrolet", modelo="Cruze", versao="1.4 Turbo Lt 16V Flex 4P Automático", ano=2018)

    resultado = inventory.buscar_veiculos(loja_id=loja.id, termo="Cruze 1.4 Turbo LT 2018")

    assert isinstance(resultado, list)
    assert len(resultado) == 1


def test_buscar_veiculos_termo_com_ano_que_nao_bate_retorna_vazio():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Busca Ano Errado")
    _make_veiculo(db, loja.id, marca="Chevrolet", modelo="Cruze", ano=2018)

    resultado = inventory.buscar_veiculos(loja_id=loja.id, termo="Cruze 2019")

    assert resultado == {"resultado": "Nenhum veículo encontrado no nosso estoque com esses filtros."}


def test_buscar_veiculos_termo_sem_correspondencia_retorna_vazio():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Inventory Vazia")
    _make_veiculo(db, loja.id, marca="Mercedes Benz", modelo="A200", versao="SD HI")

    resultado = inventory.buscar_veiculos(loja_id=loja.id, termo="Ferrari F40")

    assert resultado == {"resultado": "Nenhum veículo encontrado no nosso estoque com esses filtros."}


def test_buscar_veiculos_nao_retorna_veiculo_vendido():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Inventory Vendido")
    _make_veiculo(db, loja.id, marca="Fiat", modelo="Uno Vendido Teste", status="Vendido")
    _make_veiculo(db, loja.id, marca="Fiat", modelo="Uno Disponivel Teste")

    resultado = inventory.buscar_veiculos(loja_id=loja.id, marca="Fiat")

    modelos = [v["modelo"] for v in resultado]
    assert "Uno Disponivel Teste" in modelos
    assert "Uno Vendido Teste" not in modelos


def test_buscar_veiculos_nao_retorna_veiculo_rascunho():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Inventory Rascunho")
    _make_veiculo(db, loja.id, marca="Fiat", modelo="Argo Rascunho Teste", status_publicacao="Rascunho")

    resultado = inventory.buscar_veiculos(loja_id=loja.id, marca="Fiat", termo="Argo Rascunho Teste")

    assert resultado == {"resultado": "Nenhum veículo encontrado no nosso estoque com esses filtros."}


def test_detalhes_veiculo_vendido_devolve_mesmo_formato_de_nao_encontrado():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Inventory Detalhe Oculto")
    veiculo = _make_veiculo(db, loja.id, marca="Honda", modelo="Civic Oculto Teste", status="Vendido")

    oculto = inventory.detalhes_veiculo(loja_id=loja.id, slug=veiculo.slug)
    inexistente = inventory.detalhes_veiculo(loja_id=loja.id, slug="slug-que-nunca-existiu")

    assert oculto == inexistente == {"erro": "Veículo não encontrado na nossa base de dados."}


def test_detalhes_veiculo_retorna_campos_estruturados_novos():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Inventory Campos Novos")
    veiculo = _make_veiculo(
        db, loja.id, marca="Toyota", modelo="Hilux Campos Novos",
        cidade="São Paulo - SP", final_placa="3", blindado=True, aceita_troca=True,
        garantia_fabrica=False,
    )

    resultado = inventory.detalhes_veiculo(loja_id=loja.id, slug=veiculo.slug)

    assert resultado["cidade"] == "São Paulo - SP"
    assert resultado["final_placa"] == "3"
    assert resultado["blindado"] is True
    assert resultado["aceita_troca"] is True
    assert resultado["garantia_fabrica"] is False


def test_listar_fotos_veiculo_limita_quantidade_enviada():
    """Veículos reais têm até ~19 fotos — mandar todas de uma vez via WhatsApp é rajada
    demais (visto em produção: WhatsApp aplicou restrição depois de um envio automatizado
    mal comportado). listar_fotos_veiculo precisa cortar num teto razoável."""
    db = SessionLocal()
    loja = _make_loja(db, "Loja Fotos Muitas")
    veiculo = _make_veiculo(db, loja.id, marca="Volkswagen", modelo="Nivus Muitas Fotos")
    for i in range(19):
        db.add(ImagemVeiculo(veiculo_id=veiculo.id, url_imagem=f"http://x/{i}.jpg", ordem=i))
    db.commit()

    resultado = inventory.listar_fotos_veiculo(loja_id=loja.id, slug=veiculo.slug)

    assert len(resultado["fotos"]) == inventory.MAX_FOTOS_ENVIADAS
    assert inventory.MAX_FOTOS_ENVIADAS < 19


def test_listar_fotos_veiculo_resolve_slug_inventado_pela_ia():
    """Bug real visto em produção: mesmo instruída a nunca fazer isso, a IA às vezes chama
    enviar_fotos_veiculo com um slug "parecido" inventado a partir do nome do carro (ex:
    "cruze-1-4-turbo-lt-2018") em vez de rebuscar com buscar_veiculos pro slug de verdade
    ("cruze-1.4-turbo-lt-16v-flex-4p-automatico-flex-2018-5023413") — LLM não segue regra
    negativa com 100% de confiabilidade, então o código precisa se defender sozinho."""
    db = SessionLocal()
    loja = _make_loja(db, "Loja Slug Inventado")
    veiculo = _make_veiculo(
        db, loja.id, marca="Chevrolet", modelo="Cruze", ano=2018,
        versao="1.4 Turbo Lt 16V Flex 4P Automático",
        slug="cruze-1.4-turbo-lt-16v-flex-4p-automatico-flex-2018-5023413",
    )
    db.add(ImagemVeiculo(veiculo_id=veiculo.id, url_imagem="http://x/0.jpg", ordem=0))
    db.commit()

    resultado = inventory.listar_fotos_veiculo(loja_id=loja.id, slug="cruze-1-4-turbo-lt-2018")

    assert "erro" not in resultado
    assert len(resultado["fotos"]) == 1


def test_listar_fotos_veiculo_slug_inventado_desambigua_variantes_parecidas():
    """LT e LTZ são veículos diferentes — um slug inventado pra um não pode "vazar" fotos do
    outro. O fallback por token precisa ser exato o bastante pra distinguir "lt" de "ltz"."""
    db = SessionLocal()
    loja = _make_loja(db, "Loja Slug Desambiguacao")
    lt = _make_veiculo(
        db, loja.id, marca="Chevrolet", modelo="Cruze", ano=2018, versao="1.4 Turbo Lt",
        slug="cruze-1.4-turbo-lt-16v-flex-4p-automatico-flex-2018-5023413",
    )
    ltz = _make_veiculo(
        db, loja.id, marca="Chevrolet", modelo="Cruze", ano=2018, versao="1.4 Turbo Ltz",
        slug="cruze-1.4-turbo-ltz-16v-flex-4p-automatico-flex-2018-4789973",
    )
    db.add(ImagemVeiculo(veiculo_id=lt.id, url_imagem="http://x/lt.jpg", ordem=0))
    db.add(ImagemVeiculo(veiculo_id=ltz.id, url_imagem="http://x/ltz.jpg", ordem=0))
    db.commit()

    resultado_lt = inventory.listar_fotos_veiculo(loja_id=loja.id, slug="cruze-1-4-turbo-lt-2018")
    resultado_ltz = inventory.listar_fotos_veiculo(loja_id=loja.id, slug="cruze-1-4-turbo-ltz-2018")

    assert resultado_lt["fotos"][0]["url"] == "http://x/lt.jpg"
    assert resultado_ltz["fotos"][0]["url"] == "http://x/ltz.jpg"


def test_detalhes_veiculo_tambem_resolve_slug_inventado():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Detalhes Slug Inventado")
    _make_veiculo(
        db, loja.id, marca="Toyota", modelo="Corolla", ano=2019, versao="2.0 Xei",
        slug="corolla-2.0-xei-16v-flex-4p-automatico-flex-2019-4870641",
    )

    resultado = inventory.detalhes_veiculo(loja_id=loja.id, slug="corolla-2-0-xei-2019")

    assert "erro" not in resultado
    assert resultado["marca"] == "Toyota"


def test_listar_fotos_veiculo_slug_totalmente_diferente_nao_encontra_nada():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Slug Sem Match")
    _make_veiculo(
        db, loja.id, marca="Chevrolet", modelo="Cruze", ano=2018,
        slug="cruze-1.4-turbo-lt-16v-flex-4p-automatico-flex-2018-5023413",
    )

    resultado = inventory.listar_fotos_veiculo(loja_id=loja.id, slug="fiat-uno-2010")

    assert resultado.get("erro") == "Veículo não encontrado na nossa base de dados."
