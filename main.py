from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import os
import time
from collections import OrderedDict
from datetime import timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import rate_limit as _rate_limit
import template_helpers
from claude_agent import obter_resposta_ia
from image_utils import redimensionar_e_salvar_webp
from database import (
    STATUS_LEAD_FECHADOS,
    STATUS_LEAD_SILENCIADOS,
    SessionLocal,
    agora_utc,
    criar_lead_apos_encerramento,
    encerrar_conversa,
    obter_conversa,
    obter_conversa_atualizada_em,
    obter_lead_mais_recente,
    obter_loja_padrao,
    obter_status_lead_mais_recente,
    salvar_conversa,
)
from dealership_config import (
    DEALERSHIP_NAME,
    DEALERSHIP_PHONE,
    TEST_PHONES,
    WAHA_API_KEY,
    WAHA_BASE_URL,
    WAHA_SESSION,
    verificar_faq,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title=f"{DEALERSHIP_NAME} — WhatsApp Bot")

# Aplicado aqui (nível de app) em vez de via `uvicorn.run(proxy_headers=..., forwarded_allow_ips=...)`
# porque esses kwargs não chegam no processo real quando reload=True está ligado (o
# supervisor de reload do Uvicorn não propaga esse parâmetro pro subprocesso — confirmado
# testando com/sem reload, mesmo IP de origem). Aqui vira parte do próprio app, que já é
# recarregado corretamente a cada mudança. Sem isso, request.base_url() atrás do Caddy sempre
# reportaria http:// mesmo com HTTPS de verdade na frente, quebrando URL canônica/OG/sitemap.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY")
if not SESSION_SECRET_KEY:
    raise RuntimeError(
        "SESSION_SECRET_KEY não configurada no .env — defina uma chave aleatória antes de "
        "rodar. Sem isso, as sessões do painel admin ficariam assinadas com um valor padrão "
        "público (visível no código-fonte), permitindo forjar login."
    )
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

# O módulo mimetypes do Python não conhece .webp por padrão nesse ambiente (serve como
# text/plain) — registra explicitamente pra StaticFiles servir o content-type certo.
mimetypes.add_type("image/webp", ".webp")

MEDIA_ROOT = Path(__file__).parent / "media"
os.makedirs("media", exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

MINIATURAS_ROOT = MEDIA_ROOT / ".miniaturas"


@app.get("/media-thumb/{largura}x{altura}/{caminho:path}")
async def media_thumb(largura: int, altura: int, caminho: str):
    """Miniatura sob demanda (com cache em disco) das fotos de media/ — servir o arquivo
    original (~1000x750) num card pequeno de lista faz o navegador decodificar dezenas de
    imagens em tamanho de tela cheia à toa, travando o scroll. Tamanhos permitidos vêm de
    template_helpers.TAMANHOS_MINIATURA_LOCAL — lista fechada de propósito, pra não deixar
    qualquer w/h vindo da URL virar arquivo novo em cache sem limite."""
    if (largura, altura) not in template_helpers.TAMANHOS_MINIATURA_LOCAL:
        raise HTTPException(404)

    origem = (MEDIA_ROOT / caminho).resolve()
    if MEDIA_ROOT.resolve() not in origem.parents or not origem.is_file():
        raise HTTPException(404)

    destino = MINIATURAS_ROOT / f"{largura}x{altura}" / caminho
    if not destino.is_file():
        redimensionar_e_salvar_webp(origem.read_bytes(), destino, width=largura, height=altura)

    return FileResponse(destino, media_type="image/webp")


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

MENSAGEM_LEAD_FECHADO = (
    "Esse atendimento já foi concluído com nosso time 😊 Vou avisar um vendedor que você "
    "entrou em contato de novo — ele já vai te retornar!"
)

def _telefone_esta_limitado(telefone: str) -> bool:
    limitado = _rate_limit.esta_limitado_por_taxa(telefone, RATE_LIMIT_MAX, RATE_LIMIT_WINDOW, RATE_LIMIT_BLOCK)
    if limitado:
        logger.warning(f"[ABUSO] {telefone} bloqueado por {RATE_LIMIT_BLOCK}s (rate limit)")
    return limitado


# ---------------------------------------------------------------------------
# WAHA helpers
# ---------------------------------------------------------------------------

def para_chat_id(telefone: str) -> str:
    if "@" in telefone:
        return telefone
    return f"{telefone}@c.us"


WAHA_HEADERS = {"X-Api-Key": WAHA_API_KEY} if WAHA_API_KEY else {}


async def enviar_mensagem(telefone: str, texto: str) -> None:
    url = f"{WAHA_BASE_URL}/api/sendText"
    payload = {"chatId": para_chat_id(telefone), "text": texto, "session": WAHA_SESSION}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=WAHA_HEADERS)
        resp.raise_for_status()


async def definir_digitando(telefone: str) -> None:
    url = f"{WAHA_BASE_URL}/api/startTyping"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(url, json={"chatId": para_chat_id(telefone), "session": WAHA_SESSION}, headers=WAHA_HEADERS)
        except Exception:
            pass


def _montar_arquivo_imagem(foto: dict) -> dict | None:
    """Monta o payload `file` do WAHA a partir do arquivo local em media/ (preferencial,
    já otimizado em .webp) ou, se ainda não foi baixado, da URL remota como fallback."""
    caminho_local = foto.get("caminho_local")
    if caminho_local:
        caminho_completo = MEDIA_ROOT / caminho_local
        if caminho_completo.is_file():
            data = base64.b64encode(caminho_completo.read_bytes()).decode("ascii")
            return {"mimetype": "image/webp", "filename": caminho_completo.name, "data": data}
    url = foto.get("url")
    if url:
        return {"mimetype": "image/jpeg", "filename": "foto.jpg", "url": url}
    return None


SEGUNDOS_ENTRE_FOTOS = 1.2  # rajada de fotos sem pausa também é o tipo de padrão que
# WhatsApp (conexão não-oficial) associa a bot — um intervalo pequeno já resolve.

SEGUNDOS_MINIMOS_RESPOSTA = float(os.getenv("SEGUNDOS_MINIMOS_RESPOSTA", "5"))
# responder em texto instantaneamente, sempre, é outro padrão que a WhatsApp Web
# não-oficial associa a bot — completa só o tempo que falta pra esse mínimo (o
# processamento da IA já consome uma parte dele; se já demorou mais que isso sozinho,
# não atrasa ainda mais em cima).


async def enviar_fotos_veiculo(telefone: str, fotos: dict) -> None:
    url = f"{WAHA_BASE_URL}/api/sendImage"
    veiculo = fotos.get("veiculo") or ""
    async with httpx.AsyncClient(timeout=30) as client:
        for i, foto in enumerate(fotos.get("fotos", [])):
            payload_arquivo = _montar_arquivo_imagem(foto)
            if not payload_arquivo:
                continue
            payload = {"chatId": para_chat_id(telefone), "session": WAHA_SESSION, "file": payload_arquivo}
            if i == 0 and veiculo:
                payload["caption"] = f"📸 {veiculo}"
            try:
                if i > 0:
                    await asyncio.sleep(SEGUNDOS_ENTRE_FOTOS)
                resp = await client.post(url, json=payload, headers=WAHA_HEADERS)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"Falha ao enviar foto {i + 1} pra {telefone}: {e}")


async def processar_contato_lead_fechado(telefone: str, loja_id: int, status_anterior: str) -> None:
    """Cliente cujo lead mais recente está fechado (ver STATUS_LEAD_FECHADOS) mandou mensagem
    de novo. O bot não reengaja sozinho — manda só uma cortesia e cria um lead novo pra um vendedor
    revisar manualmente. Da mensagem seguinte em diante, o bot já responde normal nesse lead novo."""
    db = SessionLocal()
    try:
        lead = criar_lead_apos_encerramento(db, loja_id, telefone, status_anterior)
    finally:
        db.close()

    logger.info(f"[REENGAJAMENTO] {telefone} voltou após lead {status_anterior} — novo lead #{lead.id}")
    await enviar_mensagem(telefone, MENSAGEM_LEAD_FECHADO)


# ---------------------------------------------------------------------------
# Processamento principal de mensagem
# ---------------------------------------------------------------------------

def _processar_sincrono(telefone: str, texto: str, nome_exibicao: str):
    db = SessionLocal()
    try:
        atualizado_em = obter_conversa_atualizada_em(db, telefone)
        if atualizado_em:
            limite = agora_utc() - timedelta(hours=CONVERSATION_EXPIRY_HOURS)
            if atualizado_em < limite:
                encerrar_conversa(db, telefone, "expirada")
                logger.info(f"[EXPIRY] sessão de {telefone} encerrada por inatividade ({CONVERSATION_EXPIRY_HOURS}h)")

        historico = obter_conversa(db, telefone)

        if len(historico) >= MAX_TURNS * 2:
            logger.info(f"[LIMITE] {telefone} atingiu {MAX_TURNS} turnos")
            return "Sua sessão expirou. Envie *reiniciar* para começar novamente.", None, None

        texto_ia, lead_para_notificar, fotos_para_enviar = obter_resposta_ia(
            mensagens=historico,
            mensagem_usuario=texto,
            telefone=telefone,
            nome_exibicao=nome_exibicao,
        )

        historico.append({"role": "user", "content": texto})
        historico.append({"role": "assistant", "content": texto_ia})
        if len(historico) > 20:
            historico = historico[-20:]

        # busca de novo (não reusa o lead_para_notificar, que só vem preenchido quando notifica) —
        # pega o lead mais recente pra marcar a conversa, mesmo que a IA não tenha notificado nada.
        loja = obter_loja_padrao(db)
        lead = obter_lead_mais_recente(db, loja.id, telefone) if loja else None
        salvar_conversa(db, telefone, historico, lead_id=lead.id if lead else None)

        return texto_ia, lead_para_notificar, fotos_para_enviar
    finally:
        db.close()


async def processar_mensagem(telefone: str, texto: str, nome_exibicao: str) -> None:
    inicio = time.monotonic()
    await definir_digitando(telefone)
    try:
        # asyncio.to_thread só existe a partir do Python 3.9 — este ambiente roda 3.8.
        loop = asyncio.get_event_loop()
        texto_ia, _lead_para_notificar, fotos_para_enviar = await loop.run_in_executor(
            None, _processar_sincrono, telefone, texto, nome_exibicao
        )
        decorrido = time.monotonic() - inicio
        if decorrido < SEGUNDOS_MINIMOS_RESPOSTA:
            await asyncio.sleep(SEGUNDOS_MINIMOS_RESPOSTA - decorrido)
        await enviar_mensagem(telefone, texto_ia)
        if fotos_para_enviar:
            await enviar_fotos_veiculo(telefone, fotos_para_enviar)
    except Exception as exc:
        logger.error(f"Erro ao processar mensagem de {telefone}: {exc}", exc_info=True)
        try:
            await enviar_mensagem(telefone, "Desculpe, tive um problema técnico. Pode tentar novamente em instantes? 🙏")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

# WAHA às vezes entrega o mesmo evento mais de uma vez (mesmo "id" de nível
# raiz, ULID) — sem isso, o bot processa e responde 2x pra mesma mensagem.
_MAX_EVENTOS_WEBHOOK_VISTOS = 500
_eventos_webhook_vistos: "OrderedDict[str, None]" = OrderedDict()


def _evento_webhook_duplicado(event_id: str) -> bool:
    if not event_id:
        return False
    if event_id in _eventos_webhook_vistos:
        return True
    _eventos_webhook_vistos[event_id] = None
    if len(_eventos_webhook_vistos) > _MAX_EVENTOS_WEBHOOK_VISTOS:
        _eventos_webhook_vistos.popitem(last=False)
    return False


@app.post("/webhook/whatsapp")
async def webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "ok"})

    event = data.get("event", "")
    if event != "message":
        return JSONResponse({"status": "ok"})

    if _evento_webhook_duplicado(data.get("id", "")):
        return JSONResponse({"status": "ok"})

    payload = data.get("payload", {})

    if payload.get("fromMe", False):
        return JSONResponse({"status": "ok"})

    remetente_jid = payload.get("from", "")
    if not (remetente_jid.endswith("@c.us") or remetente_jid.endswith("@lid")):
        return JSONResponse({"status": "ok"})

    telefone = remetente_jid
    numero_telefone = remetente_jid.split("@")[0]
    if TEST_PHONES and numero_telefone not in TEST_PHONES and remetente_jid not in TEST_PHONES:
        return JSONResponse({"status": "ok"})

    if _telefone_esta_limitado(telefone):
        return JSONResponse({"status": "ok"})

    tem_midia = payload.get("hasMedia", False)
    texto = payload.get("body", "").strip()

    if tem_midia and not texto:
        asyncio.create_task(enviar_mensagem(telefone, "Olá! 😊 Só consigo processar mensagens de texto. Por favor, escreva sua mensagem!"))
        return JSONResponse({"status": "ok"})

    if not texto:
        return JSONResponse({"status": "ok"})

    if len(texto) > MAX_MESSAGE_LENGTH:
        return JSONResponse({"status": "ok"})

    nome_exibicao = payload.get("_data", {}).get("notifyName", "") or telefone

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None
        status_lead = obter_status_lead_mais_recente(db, loja_id, telefone) if loja_id else None
    finally:
        db.close()

    if status_lead in STATUS_LEAD_SILENCIADOS:
        if status_lead in STATUS_LEAD_FECHADOS:
            asyncio.create_task(processar_contato_lead_fechado(telefone, loja_id, status_lead))
        else:
            # transferido: já avisou "vou chamar um vendedor" — fica quieto, mesmo atendimento,
            # sem cortesia repetida nem lead novo, só esperando um humano assumir.
            logger.info(f"[SILENCIADO] {telefone} — lead transferido, aguardando vendedor assumir")
        return JSONResponse({"status": "ok"})

    if texto.lower().strip() in RESET_COMMANDS:
        db = SessionLocal()
        try:
            encerrar_conversa(db, telefone, "reiniciada")
        finally:
            db.close()
        nome = f", {nome_exibicao}" if nome_exibicao and nome_exibicao != telefone else ""
        asyncio.create_task(enviar_mensagem(telefone, f"Conversa reiniciada! 😊 Como posso te ajudar{nome}?"))
        logger.info(f"[RESET] histórico de {telefone} limpo")
        return JSONResponse({"status": "ok"})

    logger.info(f"← {telefone} ({nome_exibicao}): {texto[:80]}")

    db = SessionLocal()
    try:
        tem_historico = bool(obter_conversa(db, telefone))
    finally:
        db.close()

    resposta_faq = verificar_faq(texto, tem_historico=tem_historico)
    if resposta_faq:
        logger.info(f"[FAQ] {telefone}: respondido sem Claude")
        asyncio.create_task(enviar_mensagem(telefone, resposta_faq))
        return JSONResponse({"status": "ok"})

    asyncio.create_task(processar_mensagem(telefone, texto, nome_exibicao))
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": f"{DEALERSHIP_NAME} Bot"}


if __name__ == "__main__":
    import uvicorn
    porta = int(os.getenv("PORT", 3000))
    # HOST por padrão fica em 0.0.0.0 (todas as interfaces) pra funcionar igual em qualquer
    # máquina de dev. Em produção, o .env do servidor pode restringir isso pro IP interno que
    # o proxy reverso usa pra falar com o app (ex: HOST=172.19.0.1, o gateway da rede Docker
    # do Caddy) — assim a porta fica inacessível da internet pública, só o proxy alcança.
    host = os.getenv("HOST", "0.0.0.0")
    # Confiança em X-Forwarded-* fica no ProxyHeadersMiddleware acima (não aqui via
    # proxy_headers=/forwarded_allow_ips=) — ver comentário junto do app.add_middleware.
    uvicorn.run("main:app", host=host, port=porta, reload=True, reload_includes=["*.py", "*.env"])
