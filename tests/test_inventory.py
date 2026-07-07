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
