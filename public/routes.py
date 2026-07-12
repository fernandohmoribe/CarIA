"""
Catálogo público — sem login, pra visitante anônimo navegar pelo estoque e demonstrar
interesse num veículo. Só mostra veículo disponível+publicado (get_public_vehicles/
get_public_vehicle_by_slug), nunca rascunho/vendido — mesmo filtro que o bot já usa.
"""

import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

import rate_limit as _rate_limit
import template_helpers
from database import (
    SessionLocal,
    get_default_dealership,
    get_or_create_lead,
    get_public_vehicle_by_slug,
    get_public_vehicles,
    update_lead,
)
from dealership_config import DEALERSHIP_NAME

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
template_helpers.register(templates)

INTEREST_RATE_LIMIT_MAX = 5
INTEREST_RATE_LIMIT_WINDOW = 60
INTEREST_RATE_LIMIT_BLOCK = 300


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/veiculos", response_class=HTMLResponse)
async def catalog_list(request: Request):
    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        vehicles = get_public_vehicles(db, dealership.id if dealership else None)
        return templates.TemplateResponse(
            "catalog.html",
            {"request": request, "vehicles": vehicles, "dealership_name": DEALERSHIP_NAME},
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
            "vehicle_detail.html",
            {"request": request, "vehicle": vehicle, "dealership_name": DEALERSHIP_NAME, "enviado": False},
        )
    finally:
        db.close()


@router.post("/veiculos/{slug}/interesse")
async def catalog_interesse(request: Request, slug: str):
    client_ip = _client_ip(request)
    if _rate_limit.is_rate_limited(
        f"catalogo_interesse:{client_ip}", INTEREST_RATE_LIMIT_MAX, INTEREST_RATE_LIMIT_WINDOW, INTEREST_RATE_LIMIT_BLOCK
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
                {
                    "request": request,
                    "vehicle": vehicle,
                    "dealership_name": DEALERSHIP_NAME,
                    "enviado": False,
                    "erro": "Preencha ao menos nome e telefone.",
                },
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
            "vehicle_detail.html",
            {"request": request, "vehicle": vehicle, "dealership_name": DEALERSHIP_NAME, "enviado": True},
        )
    finally:
        db.close()
