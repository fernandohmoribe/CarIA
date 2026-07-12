from __future__ import annotations

import asyncio
import base64
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import rate_limit as _rate_limit
from claude_agent import get_ai_response
from database import (
    CLOSED_LEAD_STATUSES,
    SILENCED_LEAD_STATUSES,
    SessionLocal,
    close_conversation,
    create_lead_after_closure,
    get_conversation,
    get_conversation_updated_at,
    get_default_dealership,
    get_latest_lead,
    get_latest_lead_status,
    lead_to_dict,
    save_conversation,
)
from dealership_config import (
    DEALERSHIP_NAME,
    DEALERSHIP_PHONE,
    DEALERSHIP_STAFF_PHONE,
    TEST_PHONES,
    WAHA_API_KEY,
    WAHA_BASE_URL,
    WAHA_SESSION,
    check_faq,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title=f"{DEALERSHIP_NAME} — WhatsApp Bot")

SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY")
if not SESSION_SECRET_KEY:
    raise RuntimeError(
        "SESSION_SECRET_KEY não configurada no .env — defina uma chave aleatória antes de "
        "rodar. Sem isso, as sessões do painel admin ficariam assinadas com um valor padrão "
        "público (visível no código-fonte), permitindo forjar login."
    )
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

MEDIA_ROOT = Path(__file__).parent / "media"
os.makedirs("media", exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")

from admin.routes import router as admin_router  # noqa: E402  (depende do app/middleware acima)
from public.routes import router as public_router  # noqa: E402  (idem)

app.include_router(admin_router)
app.include_router(public_router)

# ---------------------------------------------------------------------------
# Proteções anti-abuso
# ---------------------------------------------------------------------------
RATE_LIMIT_MAX = 8
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_BLOCK = 300
MAX_MESSAGE_LENGTH = 1000
MAX_TURNS = 20
CONVERSATION_EXPIRY_HOURS = 24

RESET_COMMANDS = {"reiniciar", "recomeçar", "cancelar", "/start", "reset", "começar"}

CLOSED_LEAD_MESSAGE = (
    "Esse atendimento já foi concluído com nosso time 😊 Vou avisar um vendedor que você "
    "entrou em contato de novo — ele já vai te retornar!"
)

def is_rate_limited(phone: str) -> bool:
    limited = _rate_limit.is_rate_limited(phone, RATE_LIMIT_MAX, RATE_LIMIT_WINDOW, RATE_LIMIT_BLOCK)
    if limited:
        logger.warning(f"[ABUSO] {phone} bloqueado por {RATE_LIMIT_BLOCK}s (rate limit)")
    return limited


# ---------------------------------------------------------------------------
# WAHA helpers
# ---------------------------------------------------------------------------

def to_chat_id(phone: str) -> str:
    if "@" in phone:
        return phone
    return f"{phone}@c.us"


WAHA_HEADERS = {"X-Api-Key": WAHA_API_KEY} if WAHA_API_KEY else {}


async def send_message(phone: str, text: str) -> None:
    url = f"{WAHA_BASE_URL}/api/sendText"
    payload = {"chatId": to_chat_id(phone), "text": text, "session": WAHA_SESSION}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=WAHA_HEADERS)
        resp.raise_for_status()


async def set_typing(phone: str) -> None:
    url = f"{WAHA_BASE_URL}/api/startTyping"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(url, json={"chatId": to_chat_id(phone), "session": WAHA_SESSION}, headers=WAHA_HEADERS)
        except Exception:
            pass


def _build_image_file(foto: dict) -> dict | None:
    """Monta o payload `file` do WAHA a partir do arquivo local em media/ (preferencial,
    já otimizado em .webp) ou, se ainda não foi baixado, da URL remota como fallback."""
    local_path = foto.get("local_path")
    if local_path:
        full_path = MEDIA_ROOT / local_path
        if full_path.is_file():
            data = base64.b64encode(full_path.read_bytes()).decode("ascii")
            return {"mimetype": "image/webp", "filename": full_path.name, "data": data}
    url = foto.get("url")
    if url:
        return {"mimetype": "image/jpeg", "filename": "foto.jpg", "url": url}
    return None


async def send_vehicle_photos(phone: str, photos: dict) -> None:
    url = f"{WAHA_BASE_URL}/api/sendImage"
    veiculo = photos.get("veiculo") or ""
    async with httpx.AsyncClient(timeout=30) as client:
        for i, foto in enumerate(photos.get("fotos", [])):
            file_payload = _build_image_file(foto)
            if not file_payload:
                continue
            payload = {"chatId": to_chat_id(phone), "session": WAHA_SESSION, "file": file_payload}
            if i == 0 and veiculo:
                payload["caption"] = f"📸 {veiculo}"
            try:
                resp = await client.post(url, json=payload, headers=WAHA_HEADERS)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"Falha ao enviar foto {i + 1} pra {phone}: {e}")


async def notify_staff(lead: dict, phone: str) -> None:
    if not DEALERSHIP_STAFF_PHONE:
        return

    prioridade_emoji = "🔥 LEAD QUENTE" if lead.get("prioridade") == "quente" else "🔔 Novo lead"
    linhas = [f"{prioridade_emoji} — {DEALERSHIP_NAME}", ""]
    linhas.append(f"👤 Nome: {lead.get('nome') or '—'}")
    linhas.append(f"📱 WhatsApp: {phone}")
    linhas.append(f"📞 Telefone: {lead.get('telefone') or '—'}")
    linhas.append(f"✉️ Email: {lead.get('email') or '—'}")
    if lead.get("veiculo_interesse"):
        linhas.append(f"🚘 Interesse: {lead['veiculo_interesse']}")
    if lead.get("forma_pagamento"):
        linhas.append(f"💳 Pagamento: {lead['forma_pagamento']}")
    if lead.get("tem_troca"):
        linhas.append(f"🔄 Troca: {lead.get('veiculo_troca_desc') or 'sim'}")
    if lead.get("orcamento_aproximado"):
        linhas.append(f"💰 Orçamento: {lead['orcamento_aproximado']}")
    if lead.get("urgencia_compra"):
        linhas.append(f"⏱️ Urgência: {lead['urgencia_compra']}")
    if lead.get("uso_pretendido"):
        linhas.append(f"🎯 Uso pretendido: {lead['uso_pretendido']}")
    if lead.get("preferencia_contato"):
        linhas.append(f"📅 Preferência de contato: {lead['preferencia_contato']}")
    linhas.append(f"📊 Status: {lead.get('status', 'novo')}")
    if lead.get("resumo_executivo"):
        linhas.append("")
        linhas.append(f"📝 Resumo: {lead['resumo_executivo']}")
    linhas.append("")
    linhas.append("Ver leads: http://localhost:3000/admin/leads")

    try:
        await send_message(DEALERSHIP_STAFF_PHONE, "\n".join(linhas))
    except Exception as e:
        logger.warning(f"Não foi possível notificar o vendedor: {e}")


async def handle_closed_lead_contact(phone: str, dealership_id: int, previous_status: str) -> None:
    """Cliente cujo lead mais recente está fechado (ver CLOSED_LEAD_STATUSES) mandou mensagem
    de novo. O bot não reengaja sozinho — manda só uma cortesia e cria um lead novo pra um vendedor
    revisar manualmente. Da mensagem seguinte em diante, o bot já responde normal nesse lead novo."""
    db = SessionLocal()
    try:
        lead = create_lead_after_closure(db, dealership_id, phone, previous_status)
        lead_dict = lead_to_dict(lead)
    finally:
        db.close()

    logger.info(f"[REENGAJAMENTO] {phone} voltou após lead {previous_status} — novo lead #{lead.id}")
    await send_message(phone, CLOSED_LEAD_MESSAGE)
    await notify_staff(lead_dict, phone)


# ---------------------------------------------------------------------------
# Core message processing
# ---------------------------------------------------------------------------

def _sync_process(phone: str, text: str, push_name: str):
    db = SessionLocal()
    try:
        updated_at = get_conversation_updated_at(db, phone)
        if updated_at:
            cutoff = datetime.utcnow() - timedelta(hours=CONVERSATION_EXPIRY_HOURS)
            if updated_at < cutoff:
                close_conversation(db, phone, "expired")
                logger.info(f"[EXPIRY] sessão de {phone} encerrada por inatividade ({CONVERSATION_EXPIRY_HOURS}h)")

        history = get_conversation(db, phone)

        if len(history) >= MAX_TURNS * 2:
            logger.info(f"[LIMITE] {phone} atingiu {MAX_TURNS} turnos")
            return "Sua sessão expirou. Envie *reiniciar* para começar novamente.", None, None

        ai_text, lead_to_notify, photos_to_send = get_ai_response(
            messages=history,
            user_message=text,
            phone=phone,
            push_name=push_name,
        )

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": ai_text})
        if len(history) > 20:
            history = history[-20:]

        # busca de novo (não reusa o lead_to_notify, que só vem preenchido quando notifica) —
        # pega o lead mais recente pra marcar a conversa, mesmo que a IA não tenha notificado nada.
        dealership = get_default_dealership(db)
        lead = get_latest_lead(db, dealership.id, phone) if dealership else None
        save_conversation(db, phone, history, lead_id=lead.id if lead else None)

        return ai_text, lead_to_notify, photos_to_send
    finally:
        db.close()


async def process_message(phone: str, text: str, push_name: str) -> None:
    await set_typing(phone)
    try:
        # asyncio.to_thread só existe a partir do Python 3.9 — este ambiente roda 3.8.
        loop = asyncio.get_event_loop()
        ai_text, lead_to_notify, photos_to_send = await loop.run_in_executor(
            None, _sync_process, phone, text, push_name
        )
        await send_message(phone, ai_text)
        if photos_to_send:
            await send_vehicle_photos(phone, photos_to_send)
        if lead_to_notify:
            await notify_staff(lead_to_notify, phone)
    except Exception as exc:
        logger.error(f"Erro ao processar mensagem de {phone}: {exc}", exc_info=True)
        try:
            await send_message(phone, "Desculpe, tive um problema técnico. Pode tentar novamente em instantes? 🙏")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@app.post("/webhook/whatsapp")
async def webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "ok"})

    event = data.get("event", "")
    if event != "message":
        return JSONResponse({"status": "ok"})

    payload = data.get("payload", {})

    if payload.get("fromMe", False):
        return JSONResponse({"status": "ok"})

    from_jid = payload.get("from", "")
    if not (from_jid.endswith("@c.us") or from_jid.endswith("@lid")):
        return JSONResponse({"status": "ok"})

    phone = from_jid
    phone_num = from_jid.split("@")[0]
    if TEST_PHONES and phone_num not in TEST_PHONES and from_jid not in TEST_PHONES:
        return JSONResponse({"status": "ok"})

    if is_rate_limited(phone):
        return JSONResponse({"status": "ok"})

    has_media = payload.get("hasMedia", False)
    text = payload.get("body", "").strip()

    if has_media and not text:
        asyncio.create_task(send_message(phone, "Olá! 😊 Só consigo processar mensagens de texto. Por favor, escreva sua mensagem!"))
        return JSONResponse({"status": "ok"})

    if not text:
        return JSONResponse({"status": "ok"})

    if len(text) > MAX_MESSAGE_LENGTH:
        return JSONResponse({"status": "ok"})

    push_name = payload.get("_data", {}).get("notifyName", "") or phone

    db = SessionLocal()
    try:
        dealership = get_default_dealership(db)
        dealership_id = dealership.id if dealership else None
        lead_status = get_latest_lead_status(db, dealership_id, phone) if dealership_id else None
    finally:
        db.close()

    if lead_status in SILENCED_LEAD_STATUSES:
        if lead_status in CLOSED_LEAD_STATUSES:
            asyncio.create_task(handle_closed_lead_contact(phone, dealership_id, lead_status))
        else:
            # transferido: já avisou "vou chamar um vendedor" — fica quieto, mesmo atendimento,
            # sem cortesia repetida nem lead novo, só esperando um humano assumir.
            logger.info(f"[SILENCIADO] {phone} — lead transferido, aguardando vendedor assumir")
        return JSONResponse({"status": "ok"})

    if text.lower().strip() in RESET_COMMANDS:
        db = SessionLocal()
        try:
            close_conversation(db, phone, "reset")
        finally:
            db.close()
        nome = f", {push_name}" if push_name and push_name != phone else ""
        asyncio.create_task(send_message(phone, f"Conversa reiniciada! 😊 Como posso te ajudar{nome}?"))
        logger.info(f"[RESET] histórico de {phone} limpo")
        return JSONResponse({"status": "ok"})

    logger.info(f"← {phone} ({push_name}): {text[:80]}")

    db = SessionLocal()
    try:
        has_history = bool(get_conversation(db, phone))
    finally:
        db.close()

    faq_answer = check_faq(text, has_history=has_history)
    if faq_answer:
        logger.info(f"[FAQ] {phone}: respondido sem Claude")
        asyncio.create_task(send_message(phone, faq_answer))
        return JSONResponse({"status": "ok"})

    asyncio.create_task(process_message(phone, text, push_name))
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": f"{DEALERSHIP_NAME} Bot"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 3000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True, reload_includes=["*.py", "*.env"])
