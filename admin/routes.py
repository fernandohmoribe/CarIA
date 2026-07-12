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
    delete_news_post,
    get_all_instagram_posts,
    get_all_leads,
    get_all_news_posts,
    get_available_vehicles,
    get_conversation,
    get_conversation_history_for_lead,
    get_default_dealership,
    get_latest_lead,
    get_lead_by_id,
    get_lead_historico,
    get_news_post_by_slug,
    get_or_create_user,
    get_vehicle_by_slug,
    replace_vehicle_images,
    save_conversation,
    set_instagram_post_visibility,
    set_lead_status,
    upsert_news_post,
    upsert_vehicle,
)
from dealership_config import to_local
from image_utils import resize_and_save_webp
from slugify import generate_unique_news_slug, generate_unique_slug
import template_helpers

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

LOGIN_RATE_LIMIT_MAX = 5
LOGIN_RATE_LIMIT_WINDOW = 60
LOGIN_RATE_LIMIT_BLOCK = 300

MEDIA_ROOT = Path(__file__).parent.parent / "media"

VEHICLE_HIGHLIGHT_OPTIONS = [
    "Airbag", "Alarme", "Alarme com acionamento a distância", "Ajuste retrovisor elétrico",
    "Ar condicionado", "Ar quente", "Banco com regulagem de altura", "Bancos de couro",
    "Bluetooth", "Botão de Ignição/Start button", "Chave Inteligente/Presencial",
    "Computador de bordo", "Controle automático de velocidade", "Controle de tração",
    "Desembaçador traseiro", "Direção com Ajuste", "Encosto de cabeça traseiro",
    "Espelhamento com Smartphone (Android Auto/CarPlay)", "Faróis Full LED", "Freio ABS",
    "Freios ABS com EBD", "GPS", "Limpador traseiro", "Piloto automático",
    "Retrovisor fotocrômico", "Retrovisores elétricos", "Rodas de liga leve",
    "Sensor de estacionamento", "Sensor de pressão dos pneus", "Tela Multimídia",
    "Travas elétricas", "USB", "Vidros elétricos", "Volante com regulagem de altura",
]

VEHICLE_YES_NO_FIELDS = [
    ("blindado", "Blindado"),
    ("aceita_troca", "Aceita troca"),
    ("unico_dono", "Único dono"),
    ("revisoes_concessionaria", "Todas as revisões feitas pela concessionária"),
    ("ipva_pago", "IPVA pago"),
    ("licenciado", "Licenciado"),
    ("garantia_fabrica", "Garantia de fábrica"),
]


def _local_time(dt, fmt: str = "%d/%m/%Y %H:%M", default: str = "—") -> str:
    """Filtro Jinja — converte datetime UTC do banco pro fuso do negócio antes de exibir."""
    local = to_local(dt)
    return local.strftime(fmt) if local else default


templates.env.filters["local_time"] = _local_time
template_helpers.register(templates)


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


@router.get("/vehicles/novo")
async def vehicle_new_form(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        "vehicle_form.html",
        {
            "request": request, "vehicle": None,
            "highlight_options": VEHICLE_HIGHLIGHT_OPTIONS, "yes_no_fields": VEHICLE_YES_NO_FIELDS,
        },
    )


@router.get("/vehicles/{slug}/editar")
async def vehicle_edit_form(request: Request, slug: str):
    redirect = require_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        vehicle = get_vehicle_by_slug(db, dealership.id if dealership else None, slug)
        if not vehicle:
            return RedirectResponse(url="/admin/vehicles", status_code=302)
        return templates.TemplateResponse(
            "vehicle_form.html",
            {
                "request": request, "vehicle": vehicle,
                "highlight_options": VEHICLE_HIGHLIGHT_OPTIONS, "yes_no_fields": VEHICLE_YES_NO_FIELDS,
            },
        )
    finally:
        db.close()


async def _save_vehicle_form(request: Request, existing_slug: str | None) -> RedirectResponse:
    form = await request.form()

    def _f(name, cast=str, default=None):
        value = form.get(name)
        if value is None or value == "":
            return default
        try:
            return cast(value)
        except (TypeError, ValueError):
            return default

    highlights = list(form.getlist("highlights"))
    outros = [line.strip() for line in (form.get("outros_destaques") or "").splitlines() if line.strip()]
    highlights = highlights + outros

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        dealership_id = dealership.id if dealership else None

        brand = _f("brand", default="")
        model = _f("model", default="")
        version = _f("version")
        year = _f("year", int)

        if existing_slug:
            slug = existing_slug
        else:
            slug = generate_unique_slug(db, dealership_id, brand, model, version, year)

        data = {
            "slug": slug,
            "brand": brand,
            "model": model,
            "version": version,
            "year": year,
            "price": _f("price", float),
            "mileage": _f("mileage", int),
            "status": _f("status", default="Disponivel"),
            "publication_status": _f("publication_status", default="Publicado"),
            "body": _f("body"),
            "transmission": _f("transmission"),
            "fuel": _f("fuel"),
            "color": _f("color"),
            "spec": _f("spec"),
            "overview": _f("overview"),
            "code": _f("code"),
            "highlights": highlights,
            "cidade": _f("cidade"),
            "final_placa": _f("final_placa"),
        }
        for field_name, _label in VEHICLE_YES_NO_FIELDS:
            data[field_name] = bool(form.get(field_name))
        vehicle = upsert_vehicle(db, dealership_id, data)

        photos = [p for p in form.getlist("photos") if getattr(p, "filename", "")]
        if photos:
            images = []
            for i, photo in enumerate(photos):
                content_type = photo.content_type or ""
                if not content_type.startswith("image/"):
                    continue
                content = await photo.read()
                if len(content) > 15 * 1024 * 1024:  # 15MB, evita decodificar arquivo gigante
                    continue
                rel_path = f"vehicles/{slug}/{i}.webp"
                resize_and_save_webp(content, MEDIA_ROOT / rel_path)
                images.append(
                    {
                        "image_url": f"/media/{rel_path}",
                        "local_path": rel_path,
                        "is_cover": i == 0,
                        "sort_order": i,
                    }
                )
            if images:
                replace_vehicle_images(db, vehicle.id, images)

        return RedirectResponse(url="/admin/vehicles", status_code=302)
    finally:
        db.close()


@router.post("/vehicles/novo")
async def vehicle_create(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return await _save_vehicle_form(request, existing_slug=None)


@router.post("/vehicles/{slug}/editar")
async def vehicle_edit(request: Request, slug: str):
    redirect = require_login(request)
    if redirect:
        return redirect
    return await _save_vehicle_form(request, existing_slug=slug)


@router.post("/vehicles/{slug}/excluir")
async def vehicle_delete(request: Request, slug: str):
    redirect = require_login(request)
    if redirect:
        return redirect

    import shutil

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        vehicle = get_vehicle_by_slug(db, dealership.id if dealership else None, slug)
        if vehicle:
            db.delete(vehicle)
            db.commit()
            shutil.rmtree(MEDIA_ROOT / "vehicles" / slug, ignore_errors=True)
        return RedirectResponse(url="/admin/vehicles", status_code=302)
    finally:
        db.close()


@router.get("/novidades")
async def news_posts_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        posts = get_all_news_posts(db, dealership.id if dealership else None)
        return templates.TemplateResponse("news_posts.html", {"request": request, "posts": posts})
    finally:
        db.close()


@router.get("/novidades/novo")
async def news_post_new_form(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse("news_post_form.html", {"request": request, "post": None})


@router.get("/novidades/{slug}/editar")
async def news_post_edit_form(request: Request, slug: str):
    redirect = require_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        post = get_news_post_by_slug(db, dealership.id if dealership else None, slug, only_published=False)
        if not post:
            return RedirectResponse(url="/admin/novidades", status_code=302)
        return templates.TemplateResponse("news_post_form.html", {"request": request, "post": post})
    finally:
        db.close()


async def _save_news_post_form(request: Request, existing_slug: str | None) -> RedirectResponse:
    form = await request.form()

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        dealership_id = dealership.id if dealership else None

        titulo = (form.get("titulo") or "").strip()
        slug = existing_slug or generate_unique_news_slug(db, dealership_id, titulo)

        data = {
            "titulo": titulo,
            "slug": slug,
            "resumo": (form.get("resumo") or "").strip() or None,
            "conteudo": (form.get("conteudo") or "").strip() or None,
            "publicado": bool(form.get("publicado")),
        }

        imagem = form.get("imagem")
        if imagem is not None and getattr(imagem, "filename", ""):
            content_type = imagem.content_type or ""
            content = await imagem.read()
            if content_type.startswith("image/") and len(content) <= 15 * 1024 * 1024:
                rel_path = f"news/{slug}.webp"
                resize_and_save_webp(content, MEDIA_ROOT / rel_path)
                data["imagem_local_path"] = rel_path
                data["imagem_url"] = f"/media/{rel_path}"

        upsert_news_post(db, dealership_id, data, slug=existing_slug)
        return RedirectResponse(url="/admin/novidades", status_code=302)
    finally:
        db.close()


@router.post("/novidades/novo")
async def news_post_create(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return await _save_news_post_form(request, existing_slug=None)


@router.post("/novidades/{slug}/editar")
async def news_post_edit(request: Request, slug: str):
    redirect = require_login(request)
    if redirect:
        return redirect
    return await _save_news_post_form(request, existing_slug=slug)


@router.post("/novidades/{slug}/excluir")
async def news_post_delete(request: Request, slug: str):
    redirect = require_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        delete_news_post(db, dealership.id if dealership else None, slug)
        return RedirectResponse(url="/admin/novidades", status_code=302)
    finally:
        db.close()


@router.get("/instagram")
async def instagram_posts_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        posts = get_all_instagram_posts(db, dealership.id if dealership else None)
        return templates.TemplateResponse("instagram_posts.html", {"request": request, "posts": posts})
    finally:
        db.close()


@router.post("/instagram/{post_id}/visibilidade")
async def instagram_post_toggle_visibility(request: Request, post_id: int):
    redirect = require_login(request)
    if redirect:
        return redirect

    form = await request.form()
    visivel = bool(form.get("visivel"))

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        set_instagram_post_visibility(db, dealership.id if dealership else None, post_id, visivel)
        return RedirectResponse(url="/admin/instagram", status_code=302)
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
        template_helpers.img_src(foto.get("local_path"), foto.get("url"), 600, 450)
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
