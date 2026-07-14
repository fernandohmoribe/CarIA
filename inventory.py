"""
Tools de consulta de estoque expostas ao Claude via tool use.

Importante: lê exclusivamente o banco local (SQLite), nunca o sistema da loja
de origem em tempo real. O banco local é mantido pelo sync_inventory.py.
"""

import re

from database import SessionLocal, Veiculo, obter_veiculo_publico_por_slug

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


def _resumo(v: Veiculo) -> dict:
    return {
        "slug": v.slug,
        "marca": v.marca,
        "modelo": v.modelo,
        "versao": v.versao,
        "ano": v.ano,
        "preco": v.preco,
        "km": v.quilometragem,
        "carroceria": v.carroceria,
        "cambio": v.cambio,
        "combustivel": v.combustivel,
        "cor": v.cor,
        # string de URL solta — NUNCA vira bloco de imagem/base64 pra API (custo de visão).
        # A IA só sabe que existe uma foto de capa, não "vê" ela. Fotos de verdade vão pela
        # tool enviar_fotos_veiculo (ver CLAUDE.md).
        "foto_capa": v.url_imagem_capa,
    }


def _detalhe(v: Veiculo) -> dict:
    data = _resumo(v)
    data.update(
        {
            "especificacao": v.especificacao,
            "descricao": v.descricao,
            "destaques": v.destaques(),
            "cidade": v.cidade,
            "final_placa": v.final_placa,
            "blindado": v.blindado,
            "aceita_troca": v.aceita_troca,
            "unico_dono": v.unico_dono,
            "revisoes_pela_concessionaria": v.revisoes_concessionaria,
            "ipva_pago": v.ipva_pago,
            "licenciado": v.licenciado,
            "garantia_fabrica": v.garantia_fabrica,
        }
    )
    return data


def buscar_veiculos(
    loja_id: int,
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
        q = db.query(Veiculo).filter(
            Veiculo.loja_id == loja_id,
            Veiculo.status == "Disponivel",
            Veiculo.status_publicacao == "Publicado",
        )
        if termo:
            # Separa em qualquer caractere não-alfanumérico, não só espaço — "Mercedes-Benz"
            # (hífen) precisa virar ["Mercedes", "Benz"], senão não bate com a marca "Mercedes
            # Benz" (espaço) cadastrada no banco e o AND entre palavras zera o resultado.
            palavras = [p for p in re.split(r"\W+", termo) if p]
            for palavra in palavras:
                like = f"%{palavra}%"
                q = q.filter(
                    or_(
                        Veiculo.marca.ilike(like),
                        Veiculo.modelo.ilike(like),
                        Veiculo.versao.ilike(like),
                    )
                )
        if marca:
            q = q.filter(Veiculo.marca.ilike(f"%{marca}%"))
        if preco_min is not None:
            q = q.filter(Veiculo.preco >= preco_min)
        if preco_max is not None:
            q = q.filter(Veiculo.preco <= preco_max)
        if carroceria:
            q = q.filter(Veiculo.carroceria.ilike(f"%{carroceria}%"))
        if cambio:
            q = q.filter(Veiculo.cambio.ilike(f"%{cambio}%"))
        if combustivel:
            q = q.filter(Veiculo.combustivel.ilike(f"%{combustivel}%"))

        veiculos = q.order_by(Veiculo.preco.asc()).limit(limit).all()
        if not veiculos:
            return {"resultado": "Nenhum veículo encontrado no nosso estoque com esses filtros."}
        return [_resumo(v) for v in veiculos]
    finally:
        db.close()


def detalhes_veiculo(loja_id: int, slug: str) -> dict:
    db = SessionLocal()
    try:
        veiculo = obter_veiculo_publico_por_slug(db, loja_id, slug)
        if not veiculo:
            return {"erro": "Veículo não encontrado na nossa base de dados."}
        return _detalhe(veiculo)
    finally:
        db.close()


# Veículos reais têm ~12 fotos em média (até 19) — mandar todas de uma vez em rajada, sem
# pausa, é o tipo de padrão que o WhatsApp (via conexão não-oficial) associa a comportamento
# automatizado. Limita a uma amostra generosa o bastante pra dar uma boa impressão do carro.
MAX_FOTOS_ENVIADAS = 8


def listar_fotos_veiculo(loja_id: int, slug: str) -> dict:
    """Retorna os arquivos de foto do veículo (caminho local em media/, com URL remota
    como fallback) pra envio real via WhatsApp — nunca pra exibir como link em texto."""
    db = SessionLocal()
    try:
        veiculo = obter_veiculo_publico_por_slug(db, loja_id, slug)
        if not veiculo:
            return {"erro": "Veículo não encontrado na nossa base de dados.", "fotos": []}
        if not veiculo.imagens:
            return {"erro": "Esse veículo não tem fotos cadastradas.", "fotos": []}
        return {
            "veiculo": f"{veiculo.marca} {veiculo.modelo} {veiculo.versao or ''}".strip(),
            "fotos": [
                {"caminho_local": img.caminho_local, "url": img.url_imagem}
                for img in veiculo.imagens[:MAX_FOTOS_ENVIADAS]
            ],
        }
    finally:
        db.close()
