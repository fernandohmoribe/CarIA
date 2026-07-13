import json
import uuid
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import rate_limit as _rate_limit
from admin.auth import exigir_login, verificar_credenciais
from claude_agent import obter_resposta_ia
from database import (
    LEAD_STATUS_LABELS,
    STATUS_LEAD_MANUAIS,
    SessionLocal,
    encerrar_conversa,
    excluir_novidade,
    obter_conversa,
    obter_historico_conversa_do_lead,
    obter_historico_lead,
    obter_lead_mais_recente,
    obter_lead_por_id,
    obter_loja_padrao,
    obter_novidade_por_slug,
    obter_ou_criar_usuario,
    obter_todas_novidades,
    obter_todos_leads,
    obter_todos_posts_instagram,
    obter_veiculo_por_slug,
    obter_veiculos_disponiveis,
    salvar_conversa,
    salvar_novidade,
    salvar_veiculo,
    definir_status_lead,
    definir_visibilidade_post_instagram,
    substituir_imagens_veiculo,
)
from dealership_config import para_local
from image_utils import redimensionar_e_salvar_webp
from slugify import gerar_slug_unico, gerar_slug_unico_novidade
import template_helpers

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

LOGIN_RATE_LIMIT_MAX = 5
LOGIN_RATE_LIMIT_WINDOW = 60
LOGIN_RATE_LIMIT_BLOCK = 300

MEDIA_ROOT = Path(__file__).parent.parent / "media"

OPCOES_DESTAQUE_VEICULO = [
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

CAMPOS_SIM_NAO_VEICULO = [
    ("blindado", "Blindado"),
    ("aceita_troca", "Aceita troca"),
    ("unico_dono", "Único dono"),
    ("revisoes_concessionaria", "Todas as revisões feitas pela concessionária"),
    ("ipva_pago", "IPVA pago"),
    ("licenciado", "Licenciado"),
    ("garantia_fabrica", "Garantia de fábrica"),
]


def _horario_local(dt, fmt: str = "%d/%m/%Y %H:%M", default: str = "—") -> str:
    """Filtro Jinja — converte datetime UTC do banco pro fuso do negócio antes de exibir."""
    local = para_local(dt)
    return local.strftime(fmt) if local else default


templates.env.filters["local_time"] = _horario_local
template_helpers.registrar(templates)


@router.get("/")
async def raiz_admin(request: Request):
    redirect = exigir_login(request)
    return redirect if redirect else RedirectResponse(url="/admin/dashboard", status_code=302)


@router.get("/login")
async def formulario_login(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_enviar(request: Request, nome_usuario: str = Form(...), senha: str = Form(...)):
    client_ip = request.client.host if request.client else "unknown"
    # IP + nome_usuario juntos: limita força bruta por IP sem deixar um usuário legítimo (ex:
    # vários logins de teste na mesma sessão) esbarrar no limite de tentativas de outro usuário.
    if _rate_limit.esta_limitado_por_taxa(
        f"admin_login:{client_ip}:{nome_usuario}", LOGIN_RATE_LIMIT_MAX, LOGIN_RATE_LIMIT_WINDOW, LOGIN_RATE_LIMIT_BLOCK
    ):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Muitas tentativas de login. Tente novamente em alguns minutos."},
            status_code=429,
        )

    if verificar_credenciais(nome_usuario, senha):
        request.session["logado"] = True
        request.session["nome_usuario"] = nome_usuario
        return RedirectResponse(url="/admin/dashboard", status_code=302)
    return templates.TemplateResponse(
        request, "login.html", {"error": "Usuário ou senha inválidos."}, status_code=401
    )


@router.get("/logout")
async def sair(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=302)


@router.get("/dashboard")
async def painel(request: Request):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        leads = obter_todos_leads(db, loja.id if loja else None)
        veiculos = obter_veiculos_disponiveis(db, loja.id if loja else None)

        contagem_status = Counter(lead.status for lead in leads)
        funil = [
            {"status": s, "label": label, "count": contagem_status.get(s, 0)}
            for s, label in LEAD_STATUS_LABELS.items()
        ]

        contagem_veiculo = Counter(lead.veiculo_interesse for lead in leads if lead.veiculo_interesse)
        top_veiculos = contagem_veiculo.most_common(5)
        quentes = [lead for lead in leads if lead.prioridade == "quente"]

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "loja": loja,
                "total_leads": len(leads),
                "total_veiculos": len(veiculos),
                "funil": funil,
                "top_veiculos": top_veiculos,
                "quentes": quentes,
            },
        )
    finally:
        db.close()


@router.get("/veiculos")
async def veiculos_pagina(request: Request):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        veiculos = obter_veiculos_disponiveis(db, loja.id if loja else None)
        return templates.TemplateResponse(
            request, "vehicles.html", {"veiculos": veiculos, "loja": loja}
        )
    finally:
        db.close()


@router.get("/veiculos/novo")
async def veiculo_formulario_novo(request: Request):
    redirect = exigir_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "vehicle_form.html",
        {
            "veiculo": None,
            "highlight_options": OPCOES_DESTAQUE_VEICULO, "yes_no_fields": CAMPOS_SIM_NAO_VEICULO,
        },
    )


@router.get("/veiculos/{slug}/editar")
async def veiculo_formulario_editar(request: Request, slug: str):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        veiculo = obter_veiculo_por_slug(db, loja.id if loja else None, slug)
        if not veiculo:
            return RedirectResponse(url="/admin/veiculos", status_code=302)
        return templates.TemplateResponse(
            request,
            "vehicle_form.html",
            {
                "veiculo": veiculo,
                "highlight_options": OPCOES_DESTAQUE_VEICULO, "yes_no_fields": CAMPOS_SIM_NAO_VEICULO,
            },
        )
    finally:
        db.close()


async def _veiculo_salvar_formulario(request: Request, slug_existente: str | None) -> RedirectResponse:
    form = await request.form()

    def _f(name, cast=str, default=None):
        value = form.get(name)
        if value is None or value == "":
            return default
        try:
            return cast(value)
        except (TypeError, ValueError):
            return default

    destaques = list(form.getlist("destaques"))
    outros = [line.strip() for line in (form.get("outros_destaques") or "").splitlines() if line.strip()]
    destaques = destaques + outros

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None

        marca = _f("marca", default="")
        modelo = _f("modelo", default="")
        versao = _f("versao")
        ano = _f("ano", int)

        if slug_existente:
            slug = slug_existente
        else:
            slug = gerar_slug_unico(db, loja_id, marca, modelo, versao, ano)

        data = {
            "slug": slug,
            "marca": marca,
            "modelo": modelo,
            "versao": versao,
            "ano": ano,
            "preco": _f("preco", float),
            "quilometragem": _f("quilometragem", int),
            "status": _f("status", default="Disponivel"),
            "status_publicacao": _f("status_publicacao", default="Publicado"),
            "carroceria": _f("carroceria"),
            "cambio": _f("cambio"),
            "combustivel": _f("combustivel"),
            "cor": _f("cor"),
            "especificacao": _f("especificacao"),
            "descricao": _f("descricao"),
            "codigo": _f("codigo"),
            "destaques": destaques,
            "cidade": _f("cidade"),
            "final_placa": _f("final_placa"),
        }
        for nome_campo, _label in CAMPOS_SIM_NAO_VEICULO:
            data[nome_campo] = bool(form.get(nome_campo))
        veiculo = salvar_veiculo(db, loja_id, data)

        fotos = [p for p in form.getlist("photos") if getattr(p, "filename", "")]
        if fotos:
            imagens = []
            for i, foto in enumerate(fotos):
                content_type = foto.content_type or ""
                if not content_type.startswith("image/"):
                    continue
                conteudo = await foto.read()
                if len(conteudo) > 15 * 1024 * 1024:  # 15MB, evita decodificar arquivo gigante
                    continue
                caminho_relativo = f"vehicles/{slug}/{i}.webp"
                redimensionar_e_salvar_webp(conteudo, MEDIA_ROOT / caminho_relativo)
                imagens.append(
                    {
                        "url_imagem": f"/media/{caminho_relativo}",
                        "caminho_local": caminho_relativo,
                        "eh_capa": i == 0,
                        "ordem": i,
                    }
                )
            if imagens:
                substituir_imagens_veiculo(db, veiculo.id, imagens)

        return RedirectResponse(url="/admin/veiculos", status_code=302)
    finally:
        db.close()


@router.post("/veiculos/novo")
async def veiculo_criar(request: Request):
    redirect = exigir_login(request)
    if redirect:
        return redirect
    return await _veiculo_salvar_formulario(request, slug_existente=None)


@router.post("/veiculos/{slug}/editar")
async def veiculo_editar(request: Request, slug: str):
    redirect = exigir_login(request)
    if redirect:
        return redirect
    return await _veiculo_salvar_formulario(request, slug_existente=slug)


@router.post("/veiculos/{slug}/excluir")
async def veiculo_excluir(request: Request, slug: str):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    import shutil

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        veiculo = obter_veiculo_por_slug(db, loja.id if loja else None, slug)
        if veiculo:
            db.delete(veiculo)
            db.commit()
            shutil.rmtree(MEDIA_ROOT / "vehicles" / slug, ignore_errors=True)
        return RedirectResponse(url="/admin/veiculos", status_code=302)
    finally:
        db.close()


@router.get("/novidades")
async def novidades_pagina(request: Request):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        posts = obter_todas_novidades(db, loja.id if loja else None)
        return templates.TemplateResponse(request, "news_posts.html", {"posts": posts})
    finally:
        db.close()


@router.get("/novidades/novo")
async def novidade_formulario_novo(request: Request):
    redirect = exigir_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "news_post_form.html", {"post": None})


@router.get("/novidades/{slug}/editar")
async def novidade_formulario_editar(request: Request, slug: str):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        post = obter_novidade_por_slug(db, loja.id if loja else None, slug, apenas_publicada=False)
        if not post:
            return RedirectResponse(url="/admin/novidades", status_code=302)
        return templates.TemplateResponse(request, "news_post_form.html", {"post": post})
    finally:
        db.close()


async def _novidade_salvar_formulario(request: Request, slug_existente: str | None) -> RedirectResponse:
    form = await request.form()

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None

        titulo = (form.get("titulo") or "").strip()
        slug = slug_existente or gerar_slug_unico_novidade(db, loja_id, titulo)

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
            conteudo = await imagem.read()
            if content_type.startswith("image/") and len(conteudo) <= 15 * 1024 * 1024:
                caminho_relativo = f"news/{slug}.webp"
                redimensionar_e_salvar_webp(conteudo, MEDIA_ROOT / caminho_relativo)
                data["caminho_local_imagem"] = caminho_relativo
                data["url_imagem"] = f"/media/{caminho_relativo}"

        salvar_novidade(db, loja_id, data, slug=slug_existente)
        return RedirectResponse(url="/admin/novidades", status_code=302)
    finally:
        db.close()


@router.post("/novidades/novo")
async def novidade_criar(request: Request):
    redirect = exigir_login(request)
    if redirect:
        return redirect
    return await _novidade_salvar_formulario(request, slug_existente=None)


@router.post("/novidades/{slug}/editar")
async def novidade_editar(request: Request, slug: str):
    redirect = exigir_login(request)
    if redirect:
        return redirect
    return await _novidade_salvar_formulario(request, slug_existente=slug)


@router.post("/novidades/{slug}/excluir")
async def novidade_excluir(request: Request, slug: str):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        excluir_novidade(db, loja.id if loja else None, slug)
        return RedirectResponse(url="/admin/novidades", status_code=302)
    finally:
        db.close()


@router.get("/instagram")
async def instagram_pagina(request: Request):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        posts = obter_todos_posts_instagram(db, loja.id if loja else None)
        return templates.TemplateResponse(request, "instagram_posts.html", {"posts": posts})
    finally:
        db.close()


@router.post("/instagram/{post_id}/visibilidade")
async def instagram_alternar_visibilidade(request: Request, post_id: int):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    form = await request.form()
    visivel = bool(form.get("visivel"))

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        definir_visibilidade_post_instagram(db, loja.id if loja else None, post_id, visivel)
        return RedirectResponse(url="/admin/instagram", status_code=302)
    finally:
        db.close()


def _filtrar_leads(db, status: str = None, prioridade: str = None, q: str = None) -> list:
    loja = obter_loja_padrao(db)
    leads = obter_todos_leads(db, loja.id if loja else None)
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


def _colunas_quadro(leads: list) -> list:
    # Quadro só mostra os status que o vendedor pode setar manualmente (STATUS_LEAD_MANUAIS) —
    # "novo" e "qualificado" são definidos só pela IA, não fazem sentido como coluna arrastável.
    return [
        {"status": s, "label": LEAD_STATUS_LABELS[s], "leads": [l for l in leads if l.status == s]}
        for s in STATUS_LEAD_MANUAIS
    ]


@router.get("/leads")
async def leads_pagina(
    request: Request, status: str = None, prioridade: str = None, view: str = "lista", q: str = None
):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        leads = _filtrar_leads(db, status, prioridade, q)
        return templates.TemplateResponse(
            request,
            "leads.html",
            {
                "leads": leads,
                "filtro_status": status,
                "filtro_prioridade": prioridade,
                "filtro_q": q or "",
                "view": "quadro" if view == "quadro" else "lista",
                "colunas_quadro": _colunas_quadro(leads),
            },
        )
    finally:
        db.close()


@router.get("/leads/resultados")
async def leads_resultados(
    request: Request, status: str = None, prioridade: str = None, view: str = "lista", q: str = None
):
    """Fragmento HTML (só a tabela/quadro, sem o layout da página) usado pelo filtro em tempo
    real de leads.html via fetch — ver script no template."""
    if not request.session.get("logado"):
        return JSONResponse({"error": "Sessão expirada, recarregue a página."}, status_code=401)

    db = SessionLocal()
    try:
        leads = _filtrar_leads(db, status, prioridade, q)
        return templates.TemplateResponse(
            request,
            "_leads_results.html",
            {
                "leads": leads,
                "view": "quadro" if view == "quadro" else "lista",
                "colunas_quadro": _colunas_quadro(leads),
            },
        )
    finally:
        db.close()


@router.get("/leads/{lead_id}")
async def lead_pagina_detalhe(request: Request, lead_id: int):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        lead = obter_lead_por_id(db, lead_id)
        if not lead:
            return RedirectResponse(url="/admin/leads", status_code=302)

        historico_conversas = obter_historico_conversa_do_lead(db, lead.id)
        conversas = []
        for conv in historico_conversas:
            try:
                mensagens = json.loads(conv.mensagens_json)
            except json.JSONDecodeError:
                mensagens = []
            conversas.append({"status": conv.status, "criado_em": conv.criado_em, "mensagens": mensagens})

        historico = obter_historico_lead(db, lead.id)

        return templates.TemplateResponse(
            request,
            "lead_detail.html",
            {
                "lead": lead,
                "conversas": conversas,
                "manual_statuses": STATUS_LEAD_MANUAIS,
                "status_labels": LEAD_STATUS_LABELS,
                "historico": historico,
            },
        )
    finally:
        db.close()


@router.post("/leads/{lead_id}/status")
async def lead_atualizar_status(request: Request, lead_id: int, status: str = Form(...), observacao: str = Form("")):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    if status not in STATUS_LEAD_MANUAIS:
        return RedirectResponse(url=f"/admin/leads/{lead_id}", status_code=302)

    db = SessionLocal()
    try:
        lead = obter_lead_por_id(db, lead_id)
        if lead:
            nome_usuario = request.session.get("nome_usuario") or "admin"
            usuario = obter_ou_criar_usuario(db, nome_usuario)
            definir_status_lead(db, lead, status, usuario_id=usuario.id, observacao=observacao.strip() or None)
    finally:
        db.close()

    return RedirectResponse(url=f"/admin/leads/{lead_id}", status_code=302)


@router.post("/leads/{lead_id}/status/mover")
async def lead_mover_status(request: Request, lead_id: int):
    """Endpoint JSON usado pelo drag-and-drop do quadro de leads (ver leads.html) — diferente
    de lead_atualizar_status acima, que é form-post com redirect (usado pelo select da tela de
    detalhe do lead)."""
    if not request.session.get("logado"):
        return JSONResponse({"error": "Sessão expirada, recarregue a página."}, status_code=401)

    body = await request.json()
    status = (body.get("status") or "").strip()
    if status not in STATUS_LEAD_MANUAIS:
        return JSONResponse({"error": "Status inválido."}, status_code=400)

    db = SessionLocal()
    try:
        lead = obter_lead_por_id(db, lead_id)
        if not lead:
            return JSONResponse({"error": "Lead não encontrado."}, status_code=404)
        nome_usuario = request.session.get("nome_usuario") or "admin"
        usuario = obter_ou_criar_usuario(db, nome_usuario)
        definir_status_lead(db, lead, status, usuario_id=usuario.id)
        return JSONResponse({"ok": True})
    finally:
        db.close()


@router.get("/sincronizacao")
async def sincronizacao_pagina(request: Request, ok: str = None):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        veiculos = obter_veiculos_disponiveis(db, loja.id if loja else None)
        return templates.TemplateResponse(
            request,
            "sync.html",
            {"loja": loja, "total_veiculos": len(veiculos), "ok": ok},
        )
    finally:
        db.close()


@router.post("/sincronizacao/executar")
async def sincronizacao_executar(request: Request):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    from sync_inventory import rodar_sincronizacao

    try:
        rodar_sincronizacao()
        return RedirectResponse(url="/admin/sincronizacao?ok=1", status_code=302)
    except Exception:
        return RedirectResponse(url="/admin/sincronizacao?ok=0", status_code=302)


@router.get("/testar-bot")
async def testar_bot_pagina(request: Request):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    if "telefone_chat_teste" not in request.session:
        request.session["telefone_chat_teste"] = f"teste-interno-{uuid.uuid4().hex[:12]}@admin"

    db = SessionLocal()
    try:
        historico = obter_conversa(db, request.session["telefone_chat_teste"])
    finally:
        db.close()

    return templates.TemplateResponse(request, "test_chat.html", {"historico": historico})


@router.post("/testar-bot/enviar")
async def testar_bot_enviar(request: Request):
    if not request.session.get("logado"):
        return JSONResponse({"error": "Sessão expirada, recarregue a página."}, status_code=401)

    telefone = request.session.get("telefone_chat_teste")
    if not telefone:
        return JSONResponse({"error": "Sessão de teste não iniciada, recarregue a página."}, status_code=400)

    body = await request.json()
    texto = (body.get("message") or "").strip()[:1000]
    if not texto:
        return JSONResponse({"error": "Mensagem vazia."}, status_code=400)

    nome_usuario = request.session.get("nome_usuario") or "admin"

    db = SessionLocal()
    try:
        historico = obter_conversa(db, telefone)
    finally:
        db.close()

    texto_ia, _lead, fotos = obter_resposta_ia(
        mensagens=historico, mensagem_usuario=texto, telefone=telefone, nome_exibicao=f"Teste ({nome_usuario})"
    )

    historico.append({"role": "user", "content": texto})
    historico.append({"role": "assistant", "content": texto_ia})

    db = SessionLocal()
    try:
        # mesma lógica do main.py:_processar_sincrono — sem isso, a conversa fica sem lead_id e
        # não aparece no histórico da tela do lead (obter_historico_conversa_do_lead).
        loja = obter_loja_padrao(db)
        lead = obter_lead_mais_recente(db, loja.id, telefone) if loja else None
        salvar_conversa(db, telefone, historico, lead_id=lead.id if lead else None)
    finally:
        db.close()

    # No WhatsApp real isso vai pelo WAHA (main.py:enviar_fotos_veiculo) — aqui, como é uma
    # página web, mostra a imagem direto na tela em vez de simular um envio que não existe.
    urls_foto = [
        template_helpers.imagem_src(foto.get("caminho_local"), foto.get("url"), 600, 450)
        for foto in (fotos.get("fotos", []) if fotos else [])
    ]

    return JSONResponse({"reply": texto_ia, "photos": urls_foto})


@router.post("/testar-bot/reiniciar")
async def testar_bot_reiniciar(request: Request):
    redirect = exigir_login(request)
    if redirect:
        return redirect

    telefone = request.session.get("telefone_chat_teste")
    if telefone:
        db = SessionLocal()
        try:
            encerrar_conversa(db, telefone, "reiniciada")
        finally:
            db.close()

    # gera um telefone novo — cada reinício simula um cliente diferente. Sem isso, "reiniciar"
    # só limpava as mensagens mas mantinha o mesmo telefone, então testar como "João" depois de
    # "Fernando" atualizava o MESMO lead (achava o lead existente por telefone e sobrescrevia o
    # nome), em vez de criar um lead novo pra cada teste.
    request.session["telefone_chat_teste"] = f"teste-interno-{uuid.uuid4().hex[:12]}@admin"

    return RedirectResponse(url="/admin/testar-bot", status_code=302)
