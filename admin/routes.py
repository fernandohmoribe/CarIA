import json
import uuid
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import rate_limit as _rate_limit
from admin.auth import check_credentials, require_login
from claude_agent import get_ai_response
from database import (
    LEAD_STATUS_LABELS,
    MANUAL_LEAD_STATUSES,
    SessionLocal,
    close_conversation,
    get_all_leads,
    get_available_vehicles,
    get_conversation,
    get_conversation_history_for_lead,
    get_default_dealership,
    get_latest_lead,
    get_lead_by_id,
    get_lead_historico,
    get_or_create_user,
    save_conversation,
    set_lead_status,
)
from dealership_config import to_local

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

LOGIN_RATE_LIMIT_MAX = 5
LOGIN_RATE_LIMIT_WINDOW = 60
LOGIN_RATE_LIMIT_BLOCK = 300


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
    client_ip = request.client.host if request.client else "unknown"
    # IP + username juntos: limita força bruta por IP sem deixar um usuário legítimo (ex: vários
    # logins de teste na mesma sessão) esbarrar no limite de tentativas de outro usuário.
    if _rate_limit.is_rate_limited(
        f"admin_login:{client_ip}:{username}", LOGIN_RATE_LIMIT_MAX, LOGIN_RATE_LIMIT_WINDOW, LOGIN_RATE_LIMIT_BLOCK
    ):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Muitas tentativas de login. Tente novamente em alguns minutos."},
            status_code=429,
        )

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


def _filter_leads(db, status: str = None, prioridade: str = None, q: str = None) -> list:
    dealership = get_default_dealership(db)
    leads = get_all_leads(db, dealership.id if dealership else None)
    if status:
        leads = [lead for lead in leads if lead.status == status]
    if prioridade:
        leads = [lead for lead in leads if lead.prioridade == prioridade]
    if q:
        termo = q.strip().lower()
        leads = [
            lead for lead in leads
            if termo in (lead.nome or "").lower() or termo in (lead.veiculo_interesse or "").lower()
        ]
    return leads


def _board_columns(leads: list) -> list:
    # Board só mostra os status que o vendedor pode setar manualmente (MANUAL_LEAD_STATUSES) —
    # "novo" e "qualificado" são definidos só pela IA, não fazem sentido como coluna arrastável.
    return [
        {"status": s, "label": LEAD_STATUS_LABELS[s], "leads": [l for l in leads if l.status == s]}
        for s in MANUAL_LEAD_STATUSES
    ]


@router.get("/leads")
async def leads_page(
    request: Request, status: str = None, prioridade: str = None, view: str = "lista", q: str = None
):
    redirect = require_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        leads = _filter_leads(db, status, prioridade, q)
        return templates.TemplateResponse(
            "leads.html",
            {
                "request": request,
                "leads": leads,
                "filtro_status": status,
                "filtro_prioridade": prioridade,
                "filtro_q": q or "",
                "view": "board" if view == "board" else "lista",
                "board_columns": _board_columns(leads),
            },
        )
    finally:
        db.close()


@router.get("/leads/resultados")
async def leads_resultados(
    request: Request, status: str = None, prioridade: str = None, view: str = "lista", q: str = None
):
    """Fragmento HTML (só a tabela/board, sem o layout da página) usado pelo filtro em tempo
    real de leads.html via fetch — ver script no template."""
    if not request.session.get("logged_in"):
        return JSONResponse({"error": "Sessão expirada, recarregue a página."}, status_code=401)

    db = SessionLocal()
    try:
        leads = _filter_leads(db, status, prioridade, q)
        return templates.TemplateResponse(
            "_leads_results.html",
            {
                "request": request,
                "leads": leads,
                "view": "board" if view == "board" else "lista",
                "board_columns": _board_columns(leads),
            },
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


@router.post("/leads/{lead_id}/status/mover")
async def move_lead_status(request: Request, lead_id: int):
    """Endpoint JSON usado pelo drag-and-drop do board de leads (ver leads.html) — diferente
    de update_lead_status acima, que é form-post com redirect (usado pelo select da tela de
    detalhe do lead)."""
    if not request.session.get("logged_in"):
        return JSONResponse({"error": "Sessão expirada, recarregue a página."}, status_code=401)

    body = await request.json()
    status = (body.get("status") or "").strip()
    if status not in MANUAL_LEAD_STATUSES:
        return JSONResponse({"error": "Status inválido."}, status_code=400)

    db = SessionLocal()
    try:
        lead = get_lead_by_id(db, lead_id)
        if not lead:
            return JSONResponse({"error": "Lead não encontrado."}, status_code=404)
        username = request.session.get("username") or "admin"
        user = get_or_create_user(db, username)
        set_lead_status(db, lead, status, user_id=user.id)
        return JSONResponse({"ok": True})
    finally:
        db.close()


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


@router.get("/testar-bot")
async def test_chat_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    if "test_chat_phone" not in request.session:
        request.session["test_chat_phone"] = f"teste-interno-{uuid.uuid4().hex[:12]}@admin"

    db = SessionLocal()
    try:
        historico = get_conversation(db, request.session["test_chat_phone"])
    finally:
        db.close()

    return templates.TemplateResponse("test_chat.html", {"request": request, "historico": historico})


@router.post("/testar-bot/enviar")
async def test_chat_send(request: Request):
    if not request.session.get("logged_in"):
        return JSONResponse({"error": "Sessão expirada, recarregue a página."}, status_code=401)

    phone = request.session.get("test_chat_phone")
    if not phone:
        return JSONResponse({"error": "Sessão de teste não iniciada, recarregue a página."}, status_code=400)

    body = await request.json()
    text = (body.get("message") or "").strip()[:1000]
    if not text:
        return JSONResponse({"error": "Mensagem vazia."}, status_code=400)

    username = request.session.get("username") or "admin"

    db = SessionLocal()
    try:
        history = get_conversation(db, phone)
    finally:
        db.close()

    ai_text, _lead, photos = get_ai_response(
        messages=history, user_message=text, phone=phone, push_name=f"Teste ({username})"
    )

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": ai_text})

    db = SessionLocal()
    try:
        # mesma lógica do main.py:_sync_process — sem isso, a conversa fica sem lead_id e
        # não aparece no histórico da tela do lead (get_conversation_history_for_lead).
        dealership = get_default_dealership(db)
        lead = get_latest_lead(db, dealership.id, phone) if dealership else None
        save_conversation(db, phone, history, lead_id=lead.id if lead else None)
    finally:
        db.close()

    # No WhatsApp real isso vai pelo WAHA (main.py:send_vehicle_photos) — aqui, como é uma
    # página web, mostra a imagem direto na tela em vez de simular um envio que não existe.
    photo_urls = [
        _img_src(foto.get("local_path"), foto.get("url"), 600, 450)
        for foto in (photos.get("fotos", []) if photos else [])
    ]

    return JSONResponse({"reply": ai_text, "photos": photo_urls})


@router.post("/testar-bot/reiniciar")
async def test_chat_reset(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    phone = request.session.get("test_chat_phone")
    if phone:
        db = SessionLocal()
        try:
            close_conversation(db, phone, "reset")
        finally:
            db.close()

    # gera um telefone novo — cada reinício simula um cliente diferente. Sem isso, "reiniciar"
    # só limpava as mensagens mas mantinha o mesmo telefone, então testar como "João" depois de
    # "Fernando" atualizava o MESMO lead (achava o lead existente por telefone e sobrescrevia o
    # nome), em vez de criar um lead novo pra cada teste.
    request.session["test_chat_phone"] = f"teste-interno-{uuid.uuid4().hex[:12]}@admin"

    return RedirectResponse(url="/admin/testar-bot", status_code=302)
