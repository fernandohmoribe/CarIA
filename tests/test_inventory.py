from database import SessionLocal, Vehicle, get_or_create_dealership

import inventory


def _make_dealership(db, nome="Loja Inventory"):
    return get_or_create_dealership(db, nome=nome, connector_type="supabase", connector_config={})


def _make_vehicle(db, dealership_id, **kwargs):
    defaults = dict(
        slug=f"v-{kwargs.get('brand', 'x')}-{kwargs.get('model', 'x')}".lower().replace(" ", "-"),
        status="Disponivel",
        publication_status="Publicado",
        price=100000.0,
    )
    defaults.update(kwargs)
    vehicle = Vehicle(dealership_id=dealership_id, **defaults)
    db.add(vehicle)
    db.commit()
    return vehicle


def test_buscar_veiculos_termo_com_hifen_na_marca_encontra_veiculo():
    """Reproduz o bug real: a IA busca com "Mercedes-Benz A200" (hífen) mas o banco guarda a
    marca como "Mercedes Benz" (espaço) — o token hifenizado não pode zerar o resultado."""
    db = SessionLocal()
    dealership = _make_dealership(db)
    _make_vehicle(db, dealership.id, brand="Mercedes Benz", model="A200", version="SD HI")

    resultado = inventory.buscar_veiculos(dealership_id=dealership.id, termo="Mercedes-Benz A200")

    assert isinstance(resultado, list)
    assert len(resultado) == 1


def test_buscar_veiculos_termo_sem_correspondencia_retorna_vazio():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Inventory Vazia")
    _make_vehicle(db, dealership.id, brand="Mercedes Benz", model="A200", version="SD HI")

    resultado = inventory.buscar_veiculos(dealership_id=dealership.id, termo="Ferrari F40")

    assert resultado == {"resultado": "Nenhum veículo encontrado no nosso estoque com esses filtros."}


def test_buscar_veiculos_nao_retorna_veiculo_vendido():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Inventory Vendido")
    _make_vehicle(db, dealership.id, brand="Fiat", model="Uno Vendido Teste", status="Vendido")
    _make_vehicle(db, dealership.id, brand="Fiat", model="Uno Disponivel Teste")

    resultado = inventory.buscar_veiculos(dealership_id=dealership.id, marca="Fiat")

    modelos = [v["modelo"] for v in resultado]
    assert "Uno Disponivel Teste" in modelos
    assert "Uno Vendido Teste" not in modelos


def test_buscar_veiculos_nao_retorna_veiculo_rascunho():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Inventory Rascunho")
    _make_vehicle(db, dealership.id, brand="Fiat", model="Argo Rascunho Teste", publication_status="Rascunho")

    resultado = inventory.buscar_veiculos(dealership_id=dealership.id, marca="Fiat", termo="Argo Rascunho Teste")

    assert resultado == {"resultado": "Nenhum veículo encontrado no nosso estoque com esses filtros."}


def test_detalhes_veiculo_vendido_devolve_mesmo_formato_de_nao_encontrado():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Inventory Detalhe Oculto")
    vehicle = _make_vehicle(db, dealership.id, brand="Honda", model="Civic Oculto Teste", status="Vendido")

    oculto = inventory.detalhes_veiculo(dealership_id=dealership.id, slug=vehicle.slug)
    inexistente = inventory.detalhes_veiculo(dealership_id=dealership.id, slug="slug-que-nunca-existiu")

    assert oculto == inexistente == {"erro": "Veículo não encontrado na nossa base de dados."}


def test_detalhes_veiculo_retorna_campos_estruturados_novos():
    db = SessionLocal()
    dealership = _make_dealership(db, "Loja Inventory Campos Novos")
    vehicle = _make_vehicle(
        db, dealership.id, brand="Toyota", model="Hilux Campos Novos",
        cidade="São Paulo - SP", final_placa="3", blindado=True, aceita_troca=True,
        garantia_fabrica=False,
    )

    resultado = inventory.detalhes_veiculo(dealership_id=dealership.id, slug=vehicle.slug)

    assert resultado["cidade"] == "São Paulo - SP"
    assert resultado["final_placa"] == "3"
    assert resultado["blindado"] is True
    assert resultado["aceita_troca"] is True
    assert resultado["garantia_fabrica"] is False
