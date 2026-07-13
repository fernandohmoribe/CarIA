"""
Site público — sem login. Catálogo (só disponível+publicado, mesmo filtro que o bot usa),
home institucional, sobre nós, contato e novidades da loja.
"""

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import rate_limit as _rate_limit
import template_helpers
from database import (
    SessionLocal,
    atualizar_lead,
    obter_avaliacoes_google,
    obter_loja_padrao,
    obter_novidade_por_slug,
    obter_novidades_publicas,
    obter_opcoes_filtro_publico,
    obter_ou_criar_lead,
    obter_posts_instagram_visiveis,
    obter_veiculo_publico_por_slug,
    obter_veiculos_publicos,
    obter_veiculos_publicos_filtrados,
)
from dealership_config import DEALERSHIP_ADDRESS, DEALERSHIP_HOURS, DEALERSHIP_NAME, DEALERSHIP_PHONE

QUEM_SOMOS = (
    "Somos especializados na venda de veículos novos e usados, nacionais e importados. Com "
    "certeza você não só apreciará como irá comprar seu veículo conosco. Todos nossos veículos "
    "são revisados criteriosamente, possibilitando dar aos nossos clientes tranquilidade na "
    "hora da compra. Não perca tempo! Compre seu veículo com quem mais entende do assunto. "
    "Nossos vendedores terão o prazer em atendê-lo."
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
template_helpers.registrar(templates)

FORM_RATE_LIMIT_MAX = 5
FORM_RATE_LIMIT_WINDOW = 60
FORM_RATE_LIMIT_BLOCK = 300

_digitos_whatsapp = re.sub(r"\D", "", DEALERSHIP_PHONE)
WHATSAPP_LINK = f"https://wa.me/55{_digitos_whatsapp}" if _digitos_whatsapp else None


def _ip_cliente(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _contexto_base(request: Request) -> dict:
    return {
        "request": request,
        "nome_loja": DEALERSHIP_NAME,
        "telefone_loja": DEALERSHIP_PHONE,
        "endereco_loja": DEALERSHIP_ADDRESS,
        "horario_loja": DEALERSHIP_HOURS,
        "quem_somos": QUEM_SOMOS,
        "whatsapp_link": WHATSAPP_LINK,
    }


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None
        videos = obter_posts_instagram_visiveis(db, loja_id) if loja_id else []
        avaliacoes = obter_avaliacoes_google(db, loja_id) if loja_id else []
        return templates.TemplateResponse(
            request,
            "home.html",
            {**_contexto_base(request), "videos": videos, "avaliacoes": avaliacoes, "enviado": False},
        )
    finally:
        db.close()


@router.post("/consultor")
async def consultor_enviar(request: Request):
    ip_cliente = _ip_cliente(request)
    if _rate_limit.esta_limitado_por_taxa(
        f"consultor:{ip_cliente}", FORM_RATE_LIMIT_MAX, FORM_RATE_LIMIT_WINDOW, FORM_RATE_LIMIT_BLOCK
    ):
        return JSONResponse({"erro": "Muitas tentativas. Tente novamente em alguns minutos."}, status_code=429)

    form = await request.form()
    nome = (form.get("nome") or "").strip()
    email = (form.get("email") or "").strip()
    telefone_raw = (form.get("telefone") or "").strip()
    carro = (form.get("carro") or "").strip()
    observacao = (form.get("observacao") or "").strip()
    numero_telefone = re.sub(r"\D", "", telefone_raw)

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None

        if not nome or not numero_telefone:
            videos = obter_posts_instagram_visiveis(db, loja_id) if loja_id else []
            avaliacoes = obter_avaliacoes_google(db, loja_id) if loja_id else []
            return templates.TemplateResponse(
                request,
                "home.html",
                {
                    **_contexto_base(request), "videos": videos, "avaliacoes": avaliacoes,
                    "enviado": False, "erro": "Preencha ao menos nome e telefone.",
                },
                status_code=400,
            )

        lead, eh_novo = obter_ou_criar_lead(db, loja_id, numero_telefone)
        if eh_novo:
            lead.origem = "site"
            db.commit()
        atualizar_lead(
            db, lead,
            {"nome": nome, "email": email, "telefone": telefone_raw, "veiculo_interesse": carro or None,
             "observacoes": observacao or None},
        )

        if WHATSAPP_LINK:
            texto = f"Olá! Meu nome é {nome}"
            if carro:
                texto += f" e tenho interesse em: {carro}"
            if observacao:
                texto += f". {observacao}"
            return RedirectResponse(url=f"{WHATSAPP_LINK}?text={quote(texto)}", status_code=303)

        videos = obter_posts_instagram_visiveis(db, loja_id) if loja_id else []
        avaliacoes = obter_avaliacoes_google(db, loja_id) if loja_id else []
        return templates.TemplateResponse(
            request, "home.html", {**_contexto_base(request), "videos": videos, "avaliacoes": avaliacoes, "enviado": True}
        )
    finally:
        db.close()


@router.get("/sobre-nos", response_class=HTMLResponse)
async def sobre_nos(request: Request):
    return templates.TemplateResponse(request, "sobre_nos.html", _contexto_base(request))


@router.get("/contato", response_class=HTMLResponse)
async def contato(request: Request):
    return templates.TemplateResponse(request, "contato.html", {**_contexto_base(request), "enviado": False})


@router.post("/contato")
async def contato_enviar(request: Request):
    ip_cliente = _ip_cliente(request)
    if _rate_limit.esta_limitado_por_taxa(
        f"contato:{ip_cliente}", FORM_RATE_LIMIT_MAX, FORM_RATE_LIMIT_WINDOW, FORM_RATE_LIMIT_BLOCK
    ):
        return JSONResponse({"erro": "Muitas tentativas. Tente novamente em alguns minutos."}, status_code=429)

    form = await request.form()
    nome = (form.get("nome") or "").strip()
    email = (form.get("email") or "").strip()
    telefone_raw = (form.get("telefone") or "").strip()
    mensagem = (form.get("mensagem") or "").strip()
    numero_telefone = re.sub(r"\D", "", telefone_raw)

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None

        if not nome or not numero_telefone:
            return templates.TemplateResponse(
                request,
                "contato.html",
                {**_contexto_base(request), "enviado": False, "erro": "Preencha ao menos nome e telefone."},
                status_code=400,
            )

        lead, eh_novo = obter_ou_criar_lead(db, loja_id, numero_telefone)
        if eh_novo:
            lead.origem = "site"
            db.commit()
        atualizar_lead(db, lead, {"nome": nome, "email": email, "telefone": telefone_raw, "observacoes": mensagem or None})

        return templates.TemplateResponse(request, "contato.html", {**_contexto_base(request), "enviado": True})
    finally:
        db.close()


@router.get("/novidades", response_class=HTMLResponse)
async def novidades_lista(request: Request):
    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        posts = obter_novidades_publicas(db, loja.id if loja else None)
        return templates.TemplateResponse(request, "novidades.html", {**_contexto_base(request), "posts": posts})
    finally:
        db.close()


@router.get("/novidades/{slug}", response_class=HTMLResponse)
async def novidades_detalhe(request: Request, slug: str):
    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        post = obter_novidade_por_slug(db, loja.id if loja else None, slug)
        if not post:
            return HTMLResponse("Novidade não encontrada.", status_code=404)
        return templates.TemplateResponse(request, "novidade_detail.html", {**_contexto_base(request), "post": post})
    finally:
        db.close()


def _analisar_float(value: str | None) -> float | None:
    """Query string de formulário GET manda "" quando o campo de preço fica vazio — FastAPI
    rejeitaria isso com 422 se o parâmetro já fosse tipado como float, então recebe como str e
    faz a conversão aqui, tratando vazio/invalido como "sem filtro"."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


@router.get("/veiculos", response_class=HTMLResponse)
async def veiculos_lista(request: Request, marca: str | None = None, preco_min: str | None = None,
                          preco_max: str | None = None, carroceria: str | None = None,
                          cambio: str | None = None, combustivel: str | None = None):
    preco_min = _analisar_float(preco_min)
    preco_max = _analisar_float(preco_max)
    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None
        tem_filtros = any([marca, preco_min, preco_max, carroceria, cambio, combustivel])
        if tem_filtros:
            veiculos = obter_veiculos_publicos_filtrados(
                db, loja_id, marca=marca, preco_min=preco_min, preco_max=preco_max,
                carroceria=carroceria, cambio=cambio, combustivel=combustivel,
            )
        else:
            veiculos = obter_veiculos_publicos(db, loja_id)
        opcoes = obter_opcoes_filtro_publico(db, loja_id)
        return templates.TemplateResponse(
            request,
            "catalog.html",
            {
                **_contexto_base(request), "veiculos": veiculos, "opcoes": opcoes,
                "filtros": {
                    "marca": marca or "", "preco_min": preco_min, "preco_max": preco_max,
                    "carroceria": carroceria or "", "cambio": cambio or "", "combustivel": combustivel or "",
                },
            },
        )
    finally:
        db.close()


@router.get("/veiculos/{slug}", response_class=HTMLResponse)
async def veiculos_detalhe(request: Request, slug: str):
    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        veiculo = obter_veiculo_publico_por_slug(db, loja.id if loja else None, slug)
        if not veiculo:
            return HTMLResponse("Veículo não encontrado.", status_code=404)
        return templates.TemplateResponse(
            request, "vehicle_detail.html", {**_contexto_base(request), "veiculo": veiculo, "enviado": False}
        )
    finally:
        db.close()


@router.post("/veiculos/{slug}/interesse")
async def veiculo_interesse_enviar(request: Request, slug: str):
    ip_cliente = _ip_cliente(request)
    if _rate_limit.esta_limitado_por_taxa(
        f"catalogo_interesse:{ip_cliente}", FORM_RATE_LIMIT_MAX, FORM_RATE_LIMIT_WINDOW, FORM_RATE_LIMIT_BLOCK
    ):
        return JSONResponse({"erro": "Muitas tentativas. Tente novamente em alguns minutos."}, status_code=429)

    form = await request.form()
    nome = (form.get("nome") or "").strip()
    email = (form.get("email") or "").strip()
    telefone_raw = (form.get("telefone") or "").strip()
    numero_telefone = re.sub(r"\D", "", telefone_raw)

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None
        veiculo = obter_veiculo_publico_por_slug(db, loja_id, slug)
        if not veiculo:
            return HTMLResponse("Veículo não encontrado.", status_code=404)

        if not nome or not numero_telefone:
            return templates.TemplateResponse(
                request,
                "vehicle_detail.html",
                {**_contexto_base(request), "veiculo": veiculo, "enviado": False, "erro": "Preencha ao menos nome e telefone."},
                status_code=400,
            )

        lead, eh_novo = obter_ou_criar_lead(db, loja_id, numero_telefone)
        if eh_novo:
            lead.origem = "site"
            db.commit()
        atualizar_lead(
            db,
            lead,
            {
                "nome": nome,
                "email": email,
                "telefone": telefone_raw,
                "veiculo_interesse": f"{veiculo.marca} {veiculo.modelo}".strip(),
                "veiculo_slug": veiculo.slug,
            },
        )

        return templates.TemplateResponse(
            request, "vehicle_detail.html", {**_contexto_base(request), "veiculo": veiculo, "enviado": True}
        )
    finally:
        db.close()
