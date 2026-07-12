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
    get_default_dealership,
    get_google_reviews,
    get_news_post_by_slug,
    get_or_create_lead,
    get_public_filter_options,
    get_public_news_posts,
    get_public_vehicle_by_slug,
    get_public_vehicles,
    get_public_vehicles_filtered,
    get_visible_instagram_posts,
    update_lead,
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
template_helpers.register(templates)

FORM_RATE_LIMIT_MAX = 5
FORM_RATE_LIMIT_WINDOW = 60
FORM_RATE_LIMIT_BLOCK = 300

_whatsapp_digits = re.sub(r"\D", "", DEALERSHIP_PHONE)
WHATSAPP_LINK = f"https://wa.me/55{_whatsapp_digits}" if _whatsapp_digits else None


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _base_context(request: Request) -> dict:
    return {
        "request": request,
        "dealership_name": DEALERSHIP_NAME,
        "dealership_phone": DEALERSHIP_PHONE,
        "dealership_address": DEALERSHIP_ADDRESS,
        "dealership_hours": DEALERSHIP_HOURS,
        "quem_somos": QUEM_SOMOS,
        "whatsapp_link": WHATSAPP_LINK,
    }


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        dealership_id = dealership.id if dealership else None
        videos = get_visible_instagram_posts(db, dealership_id) if dealership_id else []
        reviews = get_google_reviews(db, dealership_id) if dealership_id else []
        return templates.TemplateResponse(
            "home.html",
            {**_base_context(request), "videos": videos, "reviews": reviews, "enviado": False},
        )
    finally:
        db.close()


@router.post("/consultor")
async def home_consultor(request: Request):
    client_ip = _client_ip(request)
    if _rate_limit.is_rate_limited(
        f"consultor:{client_ip}", FORM_RATE_LIMIT_MAX, FORM_RATE_LIMIT_WINDOW, FORM_RATE_LIMIT_BLOCK
    ):
        return JSONResponse({"erro": "Muitas tentativas. Tente novamente em alguns minutos."}, status_code=429)

    form = await request.form()
    nome = (form.get("nome") or "").strip()
    email = (form.get("email") or "").strip()
    telefone_raw = (form.get("telefone") or "").strip()
    carro = (form.get("carro") or "").strip()
    observacao = (form.get("observacao") or "").strip()
    phone_number = re.sub(r"\D", "", telefone_raw)

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        dealership_id = dealership.id if dealership else None

        if not nome or not phone_number:
            videos = get_visible_instagram_posts(db, dealership_id) if dealership_id else []
            reviews = get_google_reviews(db, dealership_id) if dealership_id else []
            return templates.TemplateResponse(
                "home.html",
                {
                    **_base_context(request), "videos": videos, "reviews": reviews,
                    "enviado": False, "erro": "Preencha ao menos nome e telefone.",
                },
                status_code=400,
            )

        lead, is_new = get_or_create_lead(db, dealership_id, phone_number)
        if is_new:
            lead.origem = "site"
            db.commit()
        update_lead(
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

        videos = get_visible_instagram_posts(db, dealership_id) if dealership_id else []
        reviews = get_google_reviews(db, dealership_id) if dealership_id else []
        return templates.TemplateResponse(
            "home.html", {**_base_context(request), "videos": videos, "reviews": reviews, "enviado": True}
        )
    finally:
        db.close()


@router.get("/sobre-nos", response_class=HTMLResponse)
async def sobre_nos(request: Request):
    return templates.TemplateResponse("sobre_nos.html", _base_context(request))


@router.get("/contato", response_class=HTMLResponse)
async def contato(request: Request):
    return templates.TemplateResponse("contato.html", {**_base_context(request), "enviado": False})


@router.post("/contato")
async def contato_enviar(request: Request):
    client_ip = _client_ip(request)
    if _rate_limit.is_rate_limited(
        f"contato:{client_ip}", FORM_RATE_LIMIT_MAX, FORM_RATE_LIMIT_WINDOW, FORM_RATE_LIMIT_BLOCK
    ):
        return JSONResponse({"erro": "Muitas tentativas. Tente novamente em alguns minutos."}, status_code=429)

    form = await request.form()
    nome = (form.get("nome") or "").strip()
    email = (form.get("email") or "").strip()
    telefone_raw = (form.get("telefone") or "").strip()
    mensagem = (form.get("mensagem") or "").strip()
    phone_number = re.sub(r"\D", "", telefone_raw)

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        dealership_id = dealership.id if dealership else None

        if not nome or not phone_number:
            return templates.TemplateResponse(
                "contato.html",
                {**_base_context(request), "enviado": False, "erro": "Preencha ao menos nome e telefone."},
                status_code=400,
            )

        lead, is_new = get_or_create_lead(db, dealership_id, phone_number)
        if is_new:
            lead.origem = "site"
            db.commit()
        update_lead(db, lead, {"nome": nome, "email": email, "telefone": telefone_raw, "observacoes": mensagem or None})

        return templates.TemplateResponse("contato.html", {**_base_context(request), "enviado": True})
    finally:
        db.close()


@router.get("/novidades", response_class=HTMLResponse)
async def novidades_list(request: Request):
    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        posts = get_public_news_posts(db, dealership.id if dealership else None)
        return templates.TemplateResponse("novidades.html", {**_base_context(request), "posts": posts})
    finally:
        db.close()


@router.get("/novidades/{slug}", response_class=HTMLResponse)
async def novidades_detail(request: Request, slug: str):
    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        post = get_news_post_by_slug(db, dealership.id if dealership else None, slug)
        if not post:
            return HTMLResponse("Novidade não encontrada.", status_code=404)
        return templates.TemplateResponse("novidade_detail.html", {**_base_context(request), "post": post})
    finally:
        db.close()


def _parse_float(value: str | None) -> float | None:
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
async def catalog_list(request: Request, marca: str | None = None, preco_min: str | None = None,
                        preco_max: str | None = None, carroceria: str | None = None,
                        cambio: str | None = None, combustivel: str | None = None):
    preco_min = _parse_float(preco_min)
    preco_max = _parse_float(preco_max)
    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        dealership_id = dealership.id if dealership else None
        has_filters = any([marca, preco_min, preco_max, carroceria, cambio, combustivel])
        if has_filters:
            vehicles = get_public_vehicles_filtered(
                db, dealership_id, marca=marca, preco_min=preco_min, preco_max=preco_max,
                carroceria=carroceria, cambio=cambio, combustivel=combustivel,
            )
        else:
            vehicles = get_public_vehicles(db, dealership_id)
        options = get_public_filter_options(db, dealership_id)
        return templates.TemplateResponse(
            "catalog.html",
            {
                **_base_context(request), "vehicles": vehicles, "options": options,
                "filtros": {
                    "marca": marca or "", "preco_min": preco_min, "preco_max": preco_max,
                    "carroceria": carroceria or "", "cambio": cambio or "", "combustivel": combustivel or "",
                },
            },
        )
    finally:
        db.close()


@router.get("/veiculos/{slug}", response_class=HTMLResponse)
async def catalog_detail(request: Request, slug: str):
    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        vehicle = get_public_vehicle_by_slug(db, dealership.id if dealership else None, slug)
        if not vehicle:
            return HTMLResponse("Veículo não encontrado.", status_code=404)
        return templates.TemplateResponse(
            "vehicle_detail.html", {**_base_context(request), "vehicle": vehicle, "enviado": False}
        )
    finally:
        db.close()


@router.post("/veiculos/{slug}/interesse")
async def catalog_interesse(request: Request, slug: str):
    client_ip = _client_ip(request)
    if _rate_limit.is_rate_limited(
        f"catalogo_interesse:{client_ip}", FORM_RATE_LIMIT_MAX, FORM_RATE_LIMIT_WINDOW, FORM_RATE_LIMIT_BLOCK
    ):
        return JSONResponse({"erro": "Muitas tentativas. Tente novamente em alguns minutos."}, status_code=429)

    form = await request.form()
    nome = (form.get("nome") or "").strip()
    email = (form.get("email") or "").strip()
    telefone_raw = (form.get("telefone") or "").strip()
    phone_number = re.sub(r"\D", "", telefone_raw)

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        dealership_id = dealership.id if dealership else None
        vehicle = get_public_vehicle_by_slug(db, dealership_id, slug)
        if not vehicle:
            return HTMLResponse("Veículo não encontrado.", status_code=404)

        if not nome or not phone_number:
            return templates.TemplateResponse(
                "vehicle_detail.html",
                {**_base_context(request), "vehicle": vehicle, "enviado": False, "erro": "Preencha ao menos nome e telefone."},
                status_code=400,
            )

        lead, is_new = get_or_create_lead(db, dealership_id, phone_number)
        if is_new:
            lead.origem = "site"
            db.commit()
        update_lead(
            db,
            lead,
            {
                "nome": nome,
                "email": email,
                "telefone": telefone_raw,
                "veiculo_interesse": f"{vehicle.brand} {vehicle.model}".strip(),
                "veiculo_slug": vehicle.slug,
            },
        )

        return templates.TemplateResponse(
            "vehicle_detail.html", {**_base_context(request), "vehicle": vehicle, "enviado": True}
        )
    finally:
        db.close()
