"""
Tools de consulta de estoque expostas ao Claude via tool use.

Importante: lê exclusivamente o banco local (SQLite), nunca o sistema da loja
de origem em tempo real. O banco local é mantido pelo sync_inventory.py.
"""

import re

from database import SessionLocal, Vehicle, get_vehicle_by_slug

TOOLS = [
    {
        "name": "buscar_veiculos",
        "description": (
            "Busca veículos no nosso estoque com base em filtros. Use para responder "
            "quando o cliente pergunta sobre carros disponíveis, marca, faixa de preço, "
            "tipo de carroceria, câmbio ou combustível. Retorna uma lista resumida."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "termo": {
                    "type": "string",
                    "description": (
                        "Busca livre por marca, modelo E/OU versão juntos, ex: 'BMW X5 xDrive45e' ou "
                        "'Dakota'. Use isso sempre que o cliente citar um veículo específico pelo nome — "
                        "é mais confiável do que só o filtro de marca."
                    ),
                },
                "marca": {"type": "string", "description": "Marca do veículo, ex: BMW, RAM, Toyota"},
                "preco_min": {"type": "number", "description": "Preço mínimo em reais"},
                "preco_max": {"type": "number", "description": "Preço máximo em reais"},
                "carroceria": {"type": "string", "description": "Ex: SUV, Picape, Sedã, Hatch"},
                "cambio": {"type": "string", "description": "Ex: Manual, Automático"},
                "combustivel": {"type": "string", "description": "Ex: Gasolina, Diesel, Flex, Híbrido"},
            },
        },
    },
    {
        "name": "detalhes_veiculo",
        "description": (
            "Retorna a ficha completa de um veículo específico (descrição, destaques, specs). "
            "Use quando o cliente demonstra interesse em um veículo específico e quer saber mais "
            "detalhes. NÃO inclui fotos — pra enviar fotos use a tool `enviar_fotos_veiculo`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "O slug do veículo, obtido em uma busca anterior"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "enviar_fotos_veiculo",
        "description": (
            "Envia pro cliente, como mensagens de imagem reais no WhatsApp (não links de texto), "
            "as fotos do veículo. Use sempre que o cliente pedir fotos, imagens, mais fotos ou "
            "\"quero ver o carro\". NUNCA cole URLs de fotos na mensagem de texto — chame esta tool, "
            "que busca os arquivos e manda de verdade."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "O slug do veículo, obtido em uma busca anterior"},
            },
            "required": ["slug"],
        },
    },
]


def _summary(v: Vehicle) -> dict:
    return {
        "slug": v.slug,
        "marca": v.brand,
        "modelo": v.model,
        "versao": v.version,
        "ano": v.year,
        "preco": v.price,
        "km": v.mileage,
        "carroceria": v.body,
        "cambio": v.transmission,
        "combustivel": v.fuel,
        "cor": v.color,
        # string de URL solta — NUNCA vira bloco de imagem/base64 pra API (custo de visão).
        # A IA só sabe que existe uma foto de capa, não "vê" ela. Fotos de verdade vão pela
        # tool enviar_fotos_veiculo (ver CLAUDE.md).
        "foto_capa": v.cover_image_url,
    }


def _detail(v: Vehicle) -> dict:
    data = _summary(v)
    data.update(
        {
            "spec": v.spec,
            "overview": v.overview,
            "destaques": v.highlights(),
        }
    )
    return data


def buscar_veiculos(
    dealership_id: int,
    termo: str = None,
    marca: str = None,
    preco_min: float = None,
    preco_max: float = None,
    carroceria: str = None,
    cambio: str = None,
    combustivel: str = None,
    limit: int = 50,
) -> list:
    from sqlalchemy import or_

    db = SessionLocal()
    try:
        q = db.query(Vehicle).filter(Vehicle.dealership_id == dealership_id)
        if termo:
            # Separa em qualquer caractere não-alfanumérico, não só espaço — "Mercedes-Benz"
            # (hífen) precisa virar ["Mercedes", "Benz"], senão não bate com o brand "Mercedes
            # Benz" (espaço) cadastrado no banco e o AND entre palavras zera o resultado.
            palavras = [p for p in re.split(r"\W+", termo) if p]
            for palavra in palavras:
                like = f"%{palavra}%"
                q = q.filter(
                    or_(
                        Vehicle.brand.ilike(like),
                        Vehicle.model.ilike(like),
                        Vehicle.version.ilike(like),
                    )
                )
        if marca:
            q = q.filter(Vehicle.brand.ilike(f"%{marca}%"))
        if preco_min is not None:
            q = q.filter(Vehicle.price >= preco_min)
        if preco_max is not None:
            q = q.filter(Vehicle.price <= preco_max)
        if carroceria:
            q = q.filter(Vehicle.body.ilike(f"%{carroceria}%"))
        if cambio:
            q = q.filter(Vehicle.transmission.ilike(f"%{cambio}%"))
        if combustivel:
            q = q.filter(Vehicle.fuel.ilike(f"%{combustivel}%"))

        vehicles = q.order_by(Vehicle.price.asc()).limit(limit).all()
        if not vehicles:
            return {"resultado": "Nenhum veículo encontrado no nosso estoque com esses filtros."}
        return [_summary(v) for v in vehicles]
    finally:
        db.close()


def detalhes_veiculo(dealership_id: int, slug: str) -> dict:
    db = SessionLocal()
    try:
        vehicle = get_vehicle_by_slug(db, dealership_id, slug)
        if not vehicle:
            return {"erro": "Veículo não encontrado na nossa base de dados."}
        return _detail(vehicle)
    finally:
        db.close()


def listar_fotos_veiculo(dealership_id: int, slug: str) -> dict:
    """Retorna os arquivos de foto do veículo (caminho local em media/, com URL remota
    como fallback) pra envio real via WhatsApp — nunca pra exibir como link em texto."""
    db = SessionLocal()
    try:
        vehicle = get_vehicle_by_slug(db, dealership_id, slug)
        if not vehicle:
            return {"erro": "Veículo não encontrado na nossa base de dados.", "fotos": []}
        if not vehicle.images:
            return {"erro": "Esse veículo não tem fotos cadastradas.", "fotos": []}
        return {
            "veiculo": f"{vehicle.brand} {vehicle.model} {vehicle.version or ''}".strip(),
            "fotos": [{"local_path": img.local_path, "url": img.image_url} for img in vehicle.images],
        }
    finally:
        db.close()
