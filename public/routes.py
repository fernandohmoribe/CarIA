"""
Site público — sem login. Catálogo (só disponível+publicado, mesmo filtro que o bot usa),
home institucional, sobre nós, contato e novidades da loja.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
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
    obter_veiculos_parecidos,
    obter_veiculos_publicos_filtrados,
    obter_veiculos_publicos_por_slugs,
)
from dealership_config import (
    DEALERSHIP_ADDRESS,
    DEALERSHIP_CITY,
    DEALERSHIP_HOURS,
    DEALERSHIP_NAME,
    DEALERSHIP_PHONE,
    GA_MEASUREMENT_ID,
    META_PIXEL_ID,
)

QUEM_SOMOS = (
    "Somos especializados na venda de veículos novos e usados, nacionais e importados. Com "
    "certeza você não só apreciará como irá comprar seu veículo conosco. Todos nossos veículos "
    "são revisados criteriosamente, possibilitando dar aos nossos clientes tranquilidade na "
    "hora da compra. Não perca tempo! Compre seu veículo com quem mais entende do assunto. "
    "Nossos vendedores terão o prazer em atendê-lo."
)

META_DESCRICAO_PADRAO = (
    f"Confira o estoque de veículos seminovos da {DEALERSHIP_NAME}"
    + (f" em {DEALERSHIP_CITY}" if DEALERSHIP_CITY else "")
    + ". Compare preço, ano e km, e fale com um consultor."
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
        "meta_descricao_padrao": META_DESCRICAO_PADRAO,
        "og_imagem_padrao": template_helpers.url_absoluta(request, "/static/logo.png"),
        "ga_measurement_id": GA_MEASUREMENT_ID,
        "meta_pixel_id": META_PIXEL_ID,
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


def _analisar_int(value: str | None) -> int | None:
    """Mesmo espírito de `_analisar_float`, mas pra campos que são coluna Integer (ano, km) —
    `float("2020")` funciona mas devolve 2020.0, o que é semanticamente errado aqui."""
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@router.get("/veiculos", response_class=HTMLResponse)
async def veiculos_lista(request: Request, marca: str | None = None, preco_min: str | None = None,
                          preco_max: str | None = None, carroceria: str | None = None,
                          cambio: str | None = None, combustivel: str | None = None,
                          ordenar: str | None = None, busca: str | None = None,
                          ano_min: str | None = None, ano_max: str | None = None,
                          cor: str | None = None, km_max: str | None = None):
    preco_min = _analisar_float(preco_min)
    preco_max = _analisar_float(preco_max)
    ano_min = _analisar_int(ano_min)
    ano_max = _analisar_int(ano_max)
    km_max = _analisar_int(km_max)
    busca = (busca or "").strip() or None
    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None
        veiculos = obter_veiculos_publicos_filtrados(
            db, loja_id, marca=marca, preco_min=preco_min, preco_max=preco_max,
            carroceria=carroceria, cambio=cambio, combustivel=combustivel, ordenar=ordenar,
            busca=busca, ano_min=ano_min, ano_max=ano_max, cor=cor, km_max=km_max,
        )
        opcoes = obter_opcoes_filtro_publico(db, loja_id)
        return templates.TemplateResponse(
            request,
            "catalog.html",
            {
                **_contexto_base(request), "veiculos": veiculos, "opcoes": opcoes,
                "filtros": {
                    "marca": marca or "", "preco_min": preco_min, "preco_max": preco_max,
                    "carroceria": carroceria or "", "cambio": cambio or "", "combustivel": combustivel or "",
                    "ordenar": ordenar or "preco_asc", "busca": busca or "",
                    "ano_min": ano_min, "ano_max": ano_max, "cor": cor or "", "km_max": km_max,
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
        loja_id = loja.id if loja else None
        veiculo = obter_veiculo_publico_por_slug(db, loja_id, slug)
        if not veiculo:
            return HTMLResponse("Veículo não encontrado.", status_code=404)
        veiculos_parecidos = obter_veiculos_parecidos(db, loja_id, veiculo)

        link_compartilhar = None
        if WHATSAPP_LINK:
            veiculo_url = template_helpers.url_absoluta(request, f"/veiculos/{veiculo.slug}")
            texto_compartilhar = f"Olha esse {veiculo.marca} {veiculo.modelo} que encontrei: {veiculo_url}"
            link_compartilhar = f"{WHATSAPP_LINK}?text={quote(texto_compartilhar)}"

        return templates.TemplateResponse(
            request,
            "vehicle_detail.html",
            {
                **_contexto_base(request), "veiculo": veiculo, "veiculos_parecidos": veiculos_parecidos,
                "link_compartilhar": link_compartilhar, "enviado": False,
            },
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


@router.get("/favoritos", response_class=HTMLResponse)
async def favoritos(request: Request):
    return templates.TemplateResponse(request, "favoritos.html", {**_contexto_base(request)})


@router.get("/api/favoritos")
async def api_favoritos(slugs: str = ""):
    lista_slugs = list({s.strip() for s in slugs.split(",") if s.strip()})[:50]
    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None
        veiculos = obter_veiculos_publicos_por_slugs(db, loja_id, lista_slugs)
        return JSONResponse([
            {
                "slug": v.slug, "marca": v.marca, "modelo": v.modelo, "versao": v.versao,
                "preco": v.preco, "ano": v.ano, "quilometragem": v.quilometragem,
                "imagem": template_helpers.imagem_src(v.caminho_capa, v.url_imagem_capa, 400, 300),
            }
            for v in veiculos
        ])
    finally:
        db.close()


@router.get("/avaliacao", response_class=HTMLResponse)
async def avaliacao(request: Request):
    return templates.TemplateResponse(request, "avaliacao.html", {**_contexto_base(request), "enviado": False})


@router.post("/avaliacao")
async def avaliacao_enviar(request: Request):
    ip_cliente = _ip_cliente(request)
    if _rate_limit.esta_limitado_por_taxa(
        f"avaliacao:{ip_cliente}", FORM_RATE_LIMIT_MAX, FORM_RATE_LIMIT_WINDOW, FORM_RATE_LIMIT_BLOCK
    ):
        return JSONResponse({"erro": "Muitas tentativas. Tente novamente em alguns minutos."}, status_code=429)

    form = await request.form()
    nome = (form.get("nome") or "").strip()
    email = (form.get("email") or "").strip()
    telefone_raw = (form.get("telefone") or "").strip()
    veiculo_troca_desc = (form.get("veiculo_troca_desc") or "").strip()
    numero_telefone = re.sub(r"\D", "", telefone_raw)

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None

        if not nome or not numero_telefone or not veiculo_troca_desc:
            return templates.TemplateResponse(
                request, "avaliacao.html",
                {**_contexto_base(request), "enviado": False, "erro": "Preencha nome, telefone e a descrição do seu veículo."},
                status_code=400,
            )

        lead, eh_novo = obter_ou_criar_lead(db, loja_id, numero_telefone)
        if eh_novo:
            lead.origem = "site"
            db.commit()
        atualizar_lead(
            db, lead,
            {"nome": nome, "email": email, "telefone": telefone_raw, "tem_troca": True, "veiculo_troca_desc": veiculo_troca_desc},
        )

        return templates.TemplateResponse(request, "avaliacao.html", {**_contexto_base(request), "enviado": True})
    finally:
        db.close()


_PAGINAS_ESTATICAS_SITEMAP = ["/", "/veiculos", "/sobre-nos", "/contato", "/novidades", "/avaliacao"]


@router.get("/sitemap.xml")
async def sitemap_xml(request: Request):
    """Só páginas estáticas + veículos/novidades reais (mesmo filtro disponível+publicado
    que o catálogo já usa) — /favoritos fica de fora, é 100% client-side, sem conteúdo pro
    crawler indexar."""
    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        loja_id = loja.id if loja else None
        veiculos = obter_veiculos_publicos_filtrados(db, loja_id)
        novidades = obter_novidades_publicas(db, loja_id) if loja_id else []

        urlset = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
        for caminho in _PAGINAS_ESTATICAS_SITEMAP:
            url_el = ET.SubElement(urlset, "url")
            ET.SubElement(url_el, "loc").text = template_helpers.url_absoluta(request, caminho)
        for v in veiculos:
            url_el = ET.SubElement(urlset, "url")
            ET.SubElement(url_el, "loc").text = template_helpers.url_absoluta(request, f"/veiculos/{v.slug}")
            if v.sincronizado_em:
                ET.SubElement(url_el, "lastmod").text = v.sincronizado_em.date().isoformat()
        for n in novidades:
            url_el = ET.SubElement(urlset, "url")
            ET.SubElement(url_el, "loc").text = template_helpers.url_absoluta(request, f"/novidades/{n.slug}")
            if n.criado_em:
                ET.SubElement(url_el, "lastmod").text = n.criado_em.date().isoformat()

        xml_bytes = ET.tostring(urlset, encoding="utf-8", xml_declaration=True)
        return Response(content=xml_bytes, media_type="application/xml")
    finally:
        db.close()


@router.get("/robots.txt")
async def robots_txt(request: Request):
    """Gerado na hora (não arquivo estático) pra linha Sitemap: sempre usar url_absoluta —
    continua certa sem precisar editar nada no dia em que o domínio/HTTPS entrar."""
    linhas = [
        "User-agent: *",
        "Disallow: /admin",
        "Disallow: /api/",
        "Allow: /",
        f"Sitemap: {template_helpers.url_absoluta(request, '/sitemap.xml')}",
    ]
    return PlainTextResponse("\n".join(linhas) + "\n")
