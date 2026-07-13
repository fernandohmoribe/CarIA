from database import SessionLocal, Veiculo, obter_ou_criar_loja

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
