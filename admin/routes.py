import json
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import check_credentials, require_login
from database import (
    LEAD_STATUS_LABELS,
    MANUAL_LEAD_STATUSES,
    SessionLocal,
    get_all_leads,
    get_available_vehicles,
    get_conversation_history_for_lead,
    get_default_dealership,
    get_lead_by_id,
    get_lead_historico,
    get_or_create_user,
    set_lead_status,
)
from dealership_config import to_local

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _local_time(dt, fmt: str = "%d/%m/%Y %H:%M", default: str = "—") -> str:
    """Filtro Jinja — converte datetime UTC do banco pro fuso do negócio antes de exibir."""
    local = to_local(dt)
    return local.strftime(fmt) if local else default


templates.env.filters["local_time"] = _local_time


def _transform(url: str, width: int, height: int, quality: int = 65) -> str:
    """Usa a API de transformação de imagem do Supabase pra servir thumbnails leves."""
    marker = "/storage/v1/object/public/"
    idx = url.find(marker) if url else -1
    if idx == -1:
        return url
    base = url[:idx]
    rest = url[idx + len(marker):]
    return f"{base}/storage/v1/render/image/public/{rest}?width={width}&height={height}&resize=cover&quality={quality}"


def _brl(value) -> str:
    if value is None:
        return "0,00"
    formatted = f"{value:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _img_src(local_path: str, remote_url: str, width: int, height: int, quality: int = 65) -> str:
    """Prefere a foto já baixada em media/ — só cai pro Supabase se ainda não tiver sido baixada."""
    if local_path:
        return f"/media/{local_path}"
    return _transform(remote_url, width, height, quality)


templates.env.filters["brl"] = _brl
templates.env.globals["img_src"] = _img_src


@router.get("/")
async def admin_root(request: Request):
    redirect = require_login(request)
    return redirect if redirect else RedirectResponse(url="/admin/dashboard", status_code=302)


@router.get("/login")
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_credentials(username, password):
        request.session["logged_in"] = True
        request.session["username"] = username
        return RedirectResponse(url="/admin/dashboard", status_code=302)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "Usuário ou senha inválidos."}, status_code=401
    )


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=302)


@router.get("/dashboard")
async def dashboard(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        leads = get_all_leads(db, dealership.id if dealership else None)
        vehicles = get_available_vehicles(db, dealership.id if dealership else None)

        status_counts = Counter(lead.status for lead in leads)
        funil = [
            {"status": s, "label": label, "count": status_counts.get(s, 0)}
            for s, label in LEAD_STATUS_LABELS.items()
        ]

        veiculo_counts = Counter(lead.veiculo_interesse for lead in leads if lead.veiculo_interesse)
        top_veiculos = veiculo_counts.most_common(5)
        quentes = [lead for lead in leads if lead.prioridade == "quente"]

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "dealership": dealership,
                "total_leads": len(leads),
                "total_vehicles": len(vehicles),
                "funil": funil,
                "top_veiculos": top_veiculos,
                "quentes": quentes,
            },
        )
    finally:
        db.close()


@router.get("/vehicles")
async def vehicles_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        vehicles = get_available_vehicles(db, dealership.id if dealership else None)
        return templates.TemplateResponse(
            "vehicles.html", {"request": request, "vehicles": vehicles, "dealership": dealership}
        )
    finally:
        db.close()


@router.get("/leads")
async def leads_page(request: Request, status: str = None, prioridade: str = None):
    redirect = require_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        leads = get_all_leads(db, dealership.id if dealership else None)
        if status:
            leads = [lead for lead in leads if lead.status == status]
        if prioridade:
            leads = [lead for lead in leads if lead.prioridade == prioridade]
        return templates.TemplateResponse(
            "leads.html",
            {"request": request, "leads": leads, "filtro_status": status, "filtro_prioridade": prioridade},
        )
    finally:
        db.close()


@router.get("/leads/{lead_id}")
async def lead_detail_page(request: Request, lead_id: int):
    redirect = require_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        lead = get_lead_by_id(db, lead_id)
        if not lead:
            return RedirectResponse(url="/admin/leads", status_code=302)

        history = get_conversation_history_for_lead(db, lead.id)
        conversas = []
        for conv in history:
            try:
                mensagens = json.loads(conv.messages_json)
            except json.JSONDecodeError:
                mensagens = []
            conversas.append({"status": conv.status, "created_at": conv.created_at, "mensagens": mensagens})

        historico = get_lead_historico(db, lead.id)

        return templates.TemplateResponse(
            "lead_detail.html",
            {
                "request": request,
                "lead": lead,
                "conversas": conversas,
                "manual_statuses": MANUAL_LEAD_STATUSES,
                "status_labels": LEAD_STATUS_LABELS,
                "historico": historico,
            },
        )
    finally:
        db.close()


@router.post("/leads/{lead_id}/status")
async def update_lead_status(request: Request, lead_id: int, status: str = Form(...), observacao: str = Form("")):
    redirect = require_login(request)
    if redirect:
        return redirect

    if status not in MANUAL_LEAD_STATUSES:
        return RedirectResponse(url=f"/admin/leads/{lead_id}", status_code=302)

    db = SessionLocal()
    try:
        lead = get_lead_by_id(db, lead_id)
        if lead:
            username = request.session.get("username") or "admin"
            user = get_or_create_user(db, username)
            set_lead_status(db, lead, status, user_id=user.id, observacao=observacao.strip() or None)
    finally:
        db.close()

    return RedirectResponse(url=f"/admin/leads/{lead_id}", status_code=302)


@router.get("/sync")
async def sync_page(request: Request, ok: str = None):
    redirect = require_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        vehicles = get_available_vehicles(db, dealership.id if dealership else None)
        return templates.TemplateResponse(
            "sync.html",
            {"request": request, "dealership": dealership, "total_vehicles": len(vehicles), "ok": ok},
        )
    finally:
        db.close()


@router.post("/sync/run")
async def sync_run(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    from sync_inventory import run_sync

    try:
        run_sync()
        return RedirectResponse(url="/admin/sync?ok=1", status_code=302)
    except Exception:
        return RedirectResponse(url="/admin/sync?ok=0", status_code=302)
