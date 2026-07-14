from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./db/cariar_bot.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def agora_utc() -> datetime:
    """datetime.utcnow() está deprecated (Python vai remover) — isso dá o mesmo resultado
    (naive, UTC) sem o aviso, e sem mudar o formato já gravado no banco pra datetime
    timezone-aware (que quebraria comparação com os valores antigos)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# ── Loja (multi-loja pronto, hoje só existe uma linha) ─────────────────────
class Loja(Base):
    __tablename__ = "lojas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    tipo_conector = Column(String, default="supabase")
    config_conector_json = Column(Text, default="{}")
    telefone_equipe = Column(String, default="")
    ultima_sincronizacao = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, default=agora_utc)

    def config_conector(self) -> dict:
        return json.loads(self.config_conector_json or "{}")


# ── Estoque — espelho local, sincronizado pelo sync_inventory.py ──────────
class Veiculo(Base):
    __tablename__ = "veiculos"
    __table_args__ = (UniqueConstraint("loja_id", "slug", name="uq_veiculo_loja_slug"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    loja_id = Column(Integer, ForeignKey("lojas.id"), index=True)
    id_externo = Column(String, index=True)  # id do veículo no sistema de origem
    slug = Column(String, index=True)
    codigo = Column(String)
    marca = Column(String, index=True)
    modelo = Column(String, index=True)
    versao = Column(String)
    ano = Column(Integer)
    preco = Column(Float)
    quilometragem = Column(Integer)
    status = Column(String)
    status_publicacao = Column(String)
    carroceria = Column(String)
    cambio = Column(String)
    combustivel = Column(String)
    cor = Column(String)
    especificacao = Column(String)
    descricao = Column(Text)
    destaques_json = Column(Text, default="[]")
    url_imagem_capa = Column(String)
    cidade = Column(String)
    final_placa = Column(String)
    blindado = Column(Boolean, default=False)
    aceita_troca = Column(Boolean, default=False)
    unico_dono = Column(Boolean, default=False)
    revisoes_concessionaria = Column(Boolean, default=False)
    ipva_pago = Column(Boolean, default=False)
    licenciado = Column(Boolean, default=False)
    garantia_fabrica = Column(Boolean, default=False)
    sincronizado_em = Column(DateTime, default=agora_utc)

    imagens = relationship(
        "ImagemVeiculo", back_populates="veiculo", order_by="ImagemVeiculo.ordem", cascade="all, delete-orphan"
    )

    def destaques(self) -> list:
        try:
            return json.loads(self.destaques_json or "[]")
        except json.JSONDecodeError:
            return []

    @property
    def caminho_capa(self) -> str | None:
        for img in self.imagens:
            if img.eh_capa and img.caminho_local:
                return img.caminho_local
        if self.imagens and self.imagens[0].caminho_local:
            return self.imagens[0].caminho_local
        return None


class ImagemVeiculo(Base):
    __tablename__ = "imagens_veiculo"

    id = Column(Integer, primary_key=True, autoincrement=True)
    veiculo_id = Column(Integer, ForeignKey("veiculos.id"), index=True)
    url_imagem = Column(String, nullable=False)
    caminho_local = Column(String, nullable=True)  # caminho relativo dentro de media/, se já baixada
    eh_capa = Column(Boolean, default=False)
    ordem = Column(Integer, default=0)

    veiculo = relationship("Veiculo", back_populates="imagens")


# ── Conversas ────────────────────────────────────────────────────────────
# Status possíveis: ativa | concluida | expirada | reiniciada
#
# A sessão em si continua sendo por numero_telefone (é assim que o WhatsApp funciona — uma
# thread por número). lead_id é só uma marcação de qual lead estava em pauta durante essa sessão
# — importante porque o mesmo telefone pode ter vários leads ao longo do tempo (ver
# criar_lead_apos_encerramento em database.py): sem isso, o histórico de conversa de um lead
# reaberto mostraria tudo daquele telefone desde sempre, misturado com leads antigos já fechados.
# Fica nullable porque a 1ª mensagem de uma conversa nova pode chegar antes de existir lead ainda.
class Conversa(Base):
    __tablename__ = "conversas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    numero_telefone = Column(String, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    status = Column(String, default="ativa")
    mensagens_json = Column(Text, default="[]")
    criado_em = Column(DateTime, default=agora_utc)
    atualizado_em = Column(DateTime, default=agora_utc)


# ── Leads ────────────────────────────────────────────────────────────────
# novo e qualificado são calculados pela IA a partir da conversa — não entram no controle manual.
# Os outros 5 dependem de uma ação humana (vendedor ligou, fechou venda, etc) e ficam editáveis
# no painel admin.
#
# STATUS_LEAD_SILENCIADOS: bot para de responder esse telefone (main.py checa a cada mensagem).
#   - transferido: a IA desistiu e já avisou "vou chamar um vendedor" — fica em silêncio esperando
#     um humano assumir, mas é o MESMO atendimento (conversa não é resetada, sem lead novo).
#   - contatado/convertido/perdido: também fazem parte de STATUS_LEAD_FECHADOS (ver abaixo).
#
# STATUS_LEAD_FECHADOS (subconjunto de SILENCIADOS): o assunto está genuinamente encerrado — além
#   de silenciar, reseta a conversa; se o cliente insistir depois, recebe uma cortesia e um lead
#   novo é criado pra revisão manual, em vez de reabrir o lead antigo.
LEAD_STATUS_LABELS = {
    "novo": "Novo",
    "qualificado": "Qualificado",
    "agendado": "Agendado",
    "transferido": "Transferido",
    "contatado": "Contatado",
    "convertido": "Convertido",
    "perdido": "Perdido",
}
STATUS_LEAD_MANUAIS = ["agendado", "transferido", "contatado", "convertido", "perdido"]
STATUS_LEAD_FECHADOS = {"contatado", "convertido", "perdido"}
STATUS_LEAD_SILENCIADOS = {"transferido"} | STATUS_LEAD_FECHADOS

# Prioridade: normal | quente
class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    loja_id = Column(Integer, ForeignKey("lojas.id"), index=True)
    numero_telefone = Column(String, index=True)
    nome = Column(String)
    email = Column(String)
    telefone = Column(String)

    # interesse
    veiculo_interesse = Column(String)
    veiculo_slug = Column(String)

    # qualificação automotiva
    forma_pagamento = Column(String)
    tem_troca = Column(Boolean, nullable=True)
    veiculo_troca_desc = Column(String)
    orcamento_aproximado = Column(String)
    urgencia_compra = Column(String)
    uso_pretendido = Column(String)
    como_conheceu = Column(String)

    # agendamento (só interesse, sem calendário)
    preferencia_contato = Column(String)

    # facilita o vendedor / prioriza
    resumo_executivo = Column(Text)
    prioridade = Column(String, default="normal")

    # gestão
    observacoes = Column(Text)
    status = Column(String, default="novo")
    origem = Column(String, default="whatsapp")
    criado_em = Column(DateTime, default=agora_utc)
    atualizado_em = Column(DateTime, default=agora_utc)


# ── Usuários ─────────────────────────────────────────────────────────────
# Cada pessoa tem login próprio (senha_hash) — ainda sem diferenciação de permissão entre
# admin/vendedor (ver MELHORIAS), só identidade distinta pra atribuir corretamente no
# lead_historico. senha_hash fica nullable porque o usuário especial "IA" (mudanças
# automáticas do bot) nunca loga, só existe pra ser referenciado como autor.
IA_USERNAME = "IA"


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome_usuario = Column(String, unique=True, nullable=False, index=True)
    nome = Column(String)
    senha_hash = Column(String, nullable=True)
    criado_em = Column(DateTime, default=agora_utc)


# ── Histórico de status do lead ─────────────────────────────────────────
class LeadHistorico(Base):
    __tablename__ = "lead_historico"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    status_anterior = Column(String)
    status_novo = Column(String)
    observacao = Column(Text, nullable=True)
    data = Column(DateTime, default=agora_utc)

    usuario = relationship("Usuario")


# ── Conteúdo do site público (novidades, vídeos do Instagram, avaliações Google) ──
class Novidade(Base):
    __tablename__ = "novidades"
    __table_args__ = (UniqueConstraint("loja_id", "slug", name="uq_novidade_loja_slug"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    loja_id = Column(Integer, ForeignKey("lojas.id"), index=True)
    titulo = Column(String, nullable=False)
    slug = Column(String, index=True)
    resumo = Column(String)
    conteudo = Column(Text)
    url_imagem = Column(String)
    caminho_local_imagem = Column(String)
    publicado = Column(Boolean, default=True)
    criado_em = Column(DateTime, default=agora_utc)


# Fase 2 (bloqueada até o usuário gerar o token do Instagram, ver plano) — tabela já existe
# desde já pra não precisar de migração depois, só fica vazia até o sync rodar.
class PostInstagram(Base):
    __tablename__ = "posts_instagram"
    __table_args__ = (UniqueConstraint("loja_id", "id_midia", name="uq_postinstagram_loja_midia"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    loja_id = Column(Integer, ForeignKey("lojas.id"), index=True)
    id_midia = Column(String, index=True)
    legenda = Column(Text)
    tipo_midia = Column(String)
    url_midia = Column(String)
    url_miniatura = Column(String)
    link_permanente = Column(String)
    data_hora = Column(DateTime, nullable=True)
    visivel = Column(Boolean, default=False)
    sincronizado_em = Column(DateTime, default=agora_utc)


# Fase 3 (bloqueada até o usuário gerar a chave do Google Places, ver plano).
class AvaliacaoGoogle(Base):
    __tablename__ = "avaliacoes_google"

    id = Column(Integer, primary_key=True, autoincrement=True)
    loja_id = Column(Integer, ForeignKey("lojas.id"), index=True)
    nome_autor = Column(String)
    url_foto_perfil = Column(String)
    nota = Column(Integer)
    texto = Column(Text)
    tempo_relativo = Column(String)
    sincronizado_em = Column(DateTime, default=agora_utc)


Base.metadata.create_all(bind=engine)


# ── Loja ─────────────────────────────────────────────────────────────────
def obter_ou_criar_loja(db, nome: str, tipo_conector: str, config_conector: dict, telefone_equipe: str = "") -> Loja:
    loja = db.query(Loja).filter(Loja.nome == nome).first()
    if loja:
        loja.tipo_conector = tipo_conector
        loja.config_conector_json = json.dumps(config_conector, ensure_ascii=False)
        if telefone_equipe:
            loja.telefone_equipe = telefone_equipe
        db.commit()
        db.refresh(loja)
        return loja

    loja = Loja(
        nome=nome,
        tipo_conector=tipo_conector,
        config_conector_json=json.dumps(config_conector, ensure_ascii=False),
        telefone_equipe=telefone_equipe,
    )
    db.add(loja)
    db.commit()
    db.refresh(loja)
    return loja


def obter_loja_padrao(db) -> Loja | None:
    """"Ativa" = a loja mais recente cadastrada. DESC (não ASC) de propósito: ao trocar de
    loja criamos uma linha nova preservando o histórico da antiga (obter_ou_criar_loja
    busca por nome, então nome diferente = linha nova) — se isso continuasse ordenando por
    id ASC, a loja antiga permaneceria "a" loja resolvida aqui pra sempre."""
    return db.query(Loja).order_by(Loja.id.desc()).first()


# ── Usuários ─────────────────────────────────────────────────────────────
_PBKDF2_ITERATIONS = 260_000


def gerar_hash_senha(senha: str) -> str:
    """PBKDF2-HMAC-SHA256 com salt aleatório — só stdlib, sem dependência nova pro piloto.
    Formato salvo: "salt_hex$hash_hex"."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        salt, expected_hex = senha_hash.split("$", 1)
    except (ValueError, AttributeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), expected_hex)


def obter_ou_criar_usuario(db, nome_usuario: str, nome: str | None = None) -> Usuario:
    usuario = db.query(Usuario).filter(Usuario.nome_usuario == nome_usuario).first()
    if usuario:
        return usuario
    usuario = Usuario(nome_usuario=nome_usuario, nome=nome or nome_usuario)
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


def obter_usuario_ia(db) -> Usuario:
    return obter_ou_criar_usuario(db, IA_USERNAME, "Inteligência Artificial")


def criar_usuario_com_senha(db, nome_usuario: str, senha: str, nome: str | None = None) -> Usuario:
    """Cria um login novo (ou atualiza a senha de um usuário já existente, ex: o "admin"
    criado via sessão antes de ter senha própria)."""
    usuario = db.query(Usuario).filter(Usuario.nome_usuario == nome_usuario).first()
    if usuario:
        usuario.senha_hash = gerar_hash_senha(senha)
        if nome:
            usuario.nome = nome
    else:
        usuario = Usuario(nome_usuario=nome_usuario, nome=nome or nome_usuario, senha_hash=gerar_hash_senha(senha))
        db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


def verificar_credenciais_usuario(db, nome_usuario: str, senha: str) -> bool:
    usuario = db.query(Usuario).filter(Usuario.nome_usuario == nome_usuario).first()
    if not usuario or not usuario.senha_hash:
        return False
    return verificar_senha(senha, usuario.senha_hash)


# ── Estoque ──────────────────────────────────────────────────────────────
CAMPOS_VEICULO = {
    "id_externo", "slug", "codigo", "marca", "modelo", "versao", "ano", "preco", "quilometragem",
    "status", "status_publicacao", "carroceria", "cambio", "combustivel", "cor", "especificacao",
    "descricao", "url_imagem_capa", "cidade", "final_placa", "blindado", "aceita_troca",
    "unico_dono", "revisoes_concessionaria", "ipva_pago", "licenciado", "garantia_fabrica",
}


def salvar_veiculo(db, loja_id: int, data: dict) -> Veiculo:
    veiculo = (
        db.query(Veiculo)
        .filter(Veiculo.loja_id == loja_id, Veiculo.slug == data["slug"])
        .first()
    )
    destaques_json = json.dumps(data.get("destaques", []), ensure_ascii=False)

    if not veiculo:
        veiculo = Veiculo(loja_id=loja_id)
        db.add(veiculo)

    for campo in CAMPOS_VEICULO:
        if campo in data:
            setattr(veiculo, campo, data[campo])
    veiculo.destaques_json = destaques_json
    veiculo.sincronizado_em = agora_utc()

    db.commit()
    db.refresh(veiculo)
    return veiculo


def substituir_imagens_veiculo(db, veiculo_id: int, imagens: list) -> None:
    db.query(ImagemVeiculo).filter(ImagemVeiculo.veiculo_id == veiculo_id).delete()
    for img in imagens:
        db.add(
            ImagemVeiculo(
                veiculo_id=veiculo_id,
                url_imagem=img["url_imagem"],
                caminho_local=img.get("caminho_local"),
                eh_capa=img.get("eh_capa", False),
                ordem=img.get("ordem", 0),
            )
        )
    db.commit()


def obter_veiculos_disponiveis(db, loja_id: int) -> list[Veiculo]:
    return (
        db.query(Veiculo)
        .filter(Veiculo.loja_id == loja_id)
        .order_by(Veiculo.marca.asc(), Veiculo.modelo.asc())
        .all()
    )


def obter_veiculo_por_slug(db, loja_id: int, slug: str) -> Veiculo | None:
    return (
        db.query(Veiculo)
        .filter(Veiculo.loja_id == loja_id, Veiculo.slug == slug)
        .first()
    )


ORDENACOES_CATALOGO_PUBLICO = {
    "preco_asc": lambda: Veiculo.preco.asc(),
    "preco_desc": lambda: Veiculo.preco.desc(),
    "ano_desc": lambda: Veiculo.ano.desc(),
    "km_asc": lambda: Veiculo.quilometragem.asc(),
}


def obter_veiculos_publicos_filtrados(
    db,
    loja_id: int,
    marca: str | None = None,
    preco_min: float | None = None,
    preco_max: float | None = None,
    carroceria: str | None = None,
    cambio: str | None = None,
    combustivel: str | None = None,
    ordenar: str | None = None,
) -> list[Veiculo]:
    """Só veículos disponíveis e publicados, com filtros opcionais pra tela de estoque do
    catálogo público — mesmo espírito de inventory.py::buscar_veiculos, mas parametrizado por
    query string em vez de chamado pela IA. `ordenar` escolhe a ordem (ver
    ORDENACOES_CATALOGO_PUBLICO) — cai pra "menor preço" se vier vazio/desconhecido."""
    q = db.query(Veiculo).filter(
        Veiculo.loja_id == loja_id,
        Veiculo.status == "Disponivel",
        Veiculo.status_publicacao == "Publicado",
    )
    if marca:
        q = q.filter(Veiculo.marca == marca)
    if preco_min is not None:
        q = q.filter(Veiculo.preco >= preco_min)
    if preco_max is not None:
        q = q.filter(Veiculo.preco <= preco_max)
    if carroceria:
        q = q.filter(Veiculo.carroceria == carroceria)
    if cambio:
        q = q.filter(Veiculo.cambio == cambio)
    if combustivel:
        q = q.filter(Veiculo.combustivel == combustivel)
    clausula_ordenacao = ORDENACOES_CATALOGO_PUBLICO.get(ordenar, ORDENACOES_CATALOGO_PUBLICO["preco_asc"])
    return q.order_by(clausula_ordenacao()).all()


def obter_opcoes_filtro_publico(db, loja_id: int) -> dict:
    """Valores distintos hoje no estoque disponível+publicado, pra popular os dropdowns do
    filtro sem nunca oferecer uma opção que não bate com nenhum veículo real."""
    base = db.query(Veiculo).filter(
        Veiculo.loja_id == loja_id,
        Veiculo.status == "Disponivel",
        Veiculo.status_publicacao == "Publicado",
    )
    return {
        "marcas": sorted({v[0] for v in base.with_entities(Veiculo.marca).distinct() if v[0]}),
        "carrocerias": sorted({v[0] for v in base.with_entities(Veiculo.carroceria).distinct() if v[0]}),
        "cambios": sorted({v[0] for v in base.with_entities(Veiculo.cambio).distinct() if v[0]}),
        "combustiveis": sorted({v[0] for v in base.with_entities(Veiculo.combustivel).distinct() if v[0]}),
    }


def obter_veiculo_publico_por_slug(db, loja_id: int, slug: str) -> Veiculo | None:
    """Igual obter_veiculo_por_slug, mas só retorna se disponível+publicado — um veículo
    rascunho/vendido some tanto pra IA quanto pro catálogo público, com a mesma resposta de
    "não encontrado" que um slug inexistente (não vaza que o veículo existe mas está oculto)."""
    return (
        db.query(Veiculo)
        .filter(
            Veiculo.loja_id == loja_id,
            Veiculo.slug == slug,
            Veiculo.status == "Disponivel",
            Veiculo.status_publicacao == "Publicado",
        )
        .first()
    )


# ── Conversas ────────────────────────────────────────────────────────────
def _obter_ativa(db, numero_telefone: str):
    return (
        db.query(Conversa)
        .filter(Conversa.numero_telefone == numero_telefone, Conversa.status == "ativa")
        .order_by(Conversa.criado_em.desc())
        .first()
    )


def _nova_sessao(db, numero_telefone: str, lead_id: int | None = None) -> "Conversa":
    conv = Conversa(numero_telefone=numero_telefone, status="ativa", lead_id=lead_id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def obter_conversa(db, numero_telefone: str) -> list:
    conv = _obter_ativa(db, numero_telefone)
    if not conv:
        return []
    return json.loads(conv.mensagens_json)


def obter_conversa_atualizada_em(db, numero_telefone: str):
    conv = _obter_ativa(db, numero_telefone)
    return conv.atualizado_em if conv else None


def salvar_conversa(db, numero_telefone: str, mensagens: list, lead_id: int | None = None) -> None:
    conv = _obter_ativa(db, numero_telefone)
    if not conv:
        conv = _nova_sessao(db, numero_telefone, lead_id)
    conv.mensagens_json = json.dumps(mensagens, ensure_ascii=False)
    conv.atualizado_em = agora_utc()
    if lead_id is not None and conv.lead_id is None:
        # marca com o lead assim que ele existir — a 1ª mensagem de uma conversa pode chegar
        # antes da IA ter chamado criar_ou_atualizar_lead pela primeira vez.
        conv.lead_id = lead_id
    db.commit()


def encerrar_conversa(db, numero_telefone: str, motivo: str = "concluida") -> None:
    """Fecha a sessão ativa e abre uma nova vazia (sem lead_id — a próxima salvar_conversa
    marca com o lead que estiver em pauta nesse momento, que pode ser um lead novo)."""
    conv = _obter_ativa(db, numero_telefone)
    if conv:
        conv.status = motivo
        conv.atualizado_em = agora_utc()
        db.commit()
    _nova_sessao(db, numero_telefone)


def obter_historico_conversa_do_lead(db, lead_id: int) -> list["Conversa"]:
    """Histórico de conversa escopado a UM lead específico — usado no painel, pra não misturar
    sessões de leads antigos já fechados quando o mesmo telefone gera um lead novo."""
    return (
        db.query(Conversa)
        .filter(Conversa.lead_id == lead_id)
        .order_by(Conversa.criado_em.desc())
        .all()
    )


# ── Leads ────────────────────────────────────────────────────────────────
_URGENCIA_ALTA_KEYWORDS = ("essa semana", "hoje", "urgente", "o quanto antes", "amanhã", "agora")


def _calcular_prioridade(lead: Lead) -> str:
    urgencia = (lead.urgencia_compra or "").lower()
    is_urgente = any(k in urgencia for k in _URGENCIA_ALTA_KEYWORDS)
    tem_orcamento_ou_pagamento = bool(lead.orcamento_aproximado or lead.forma_pagamento)
    quer_agendar = bool(lead.preferencia_contato)
    if is_urgente and tem_orcamento_ou_pagamento and quer_agendar:
        return "quente"
    return lead.prioridade or "normal"


def obter_ou_criar_lead(db, loja_id: int, numero_telefone: str) -> tuple[Lead, bool]:
    """Retorna (lead, is_new) — is_new indica se o registro acabou de ser criado."""
    lead = (
        db.query(Lead)
        .filter(Lead.loja_id == loja_id, Lead.numero_telefone == numero_telefone)
        .order_by(Lead.criado_em.desc())
        .first()
    )
    if lead:
        return lead, False
    lead = Lead(loja_id=loja_id, numero_telefone=numero_telefone)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead, True


CAMPOS_ATUALIZAVEIS_LEAD = {
    "nome", "email", "telefone", "veiculo_interesse", "veiculo_slug", "forma_pagamento", "tem_troca",
    "veiculo_troca_desc", "orcamento_aproximado", "urgencia_compra", "uso_pretendido",
    "como_conheceu", "preferencia_contato", "resumo_executivo", "observacoes", "status",
}


def registrar_mudanca_status(
    db, lead_id: int, usuario_id: int | None, status_anterior: str | None, status_novo: str, observacao: str | None = None
) -> None:
    """Registra uma linha no histórico só quando o status de fato muda de valor —
    chamadas que atualizam outros campos do lead sem tocar o status não geram entrada."""
    if status_anterior == status_novo:
        return
    db.add(
        LeadHistorico(
            lead_id=lead_id,
            usuario_id=usuario_id,
            status_anterior=status_anterior,
            status_novo=status_novo,
            observacao=observacao,
        )
    )
    db.commit()


def obter_historico_lead(db, lead_id: int) -> list[LeadHistorico]:
    return (
        db.query(LeadHistorico)
        .filter(LeadHistorico.lead_id == lead_id)
        .order_by(LeadHistorico.data.desc())
        .all()
    )


def atualizar_lead(db, lead: Lead, campos: dict) -> Lead:
    """Atualização feita pela IA via tool `criar_ou_atualizar_lead`. Se o status mudar de
    valor nessa chamada, registra no histórico com o usuário especial "IA"."""
    status_anterior = lead.status
    for chave, valor in campos.items():
        if chave in CAMPOS_ATUALIZAVEIS_LEAD and valor is not None:
            setattr(lead, chave, valor)
    lead.prioridade = _calcular_prioridade(lead)
    lead.atualizado_em = agora_utc()
    db.commit()
    db.refresh(lead)
    if lead.status != status_anterior:
        usuario_ia = obter_usuario_ia(db)
        registrar_mudanca_status(db, lead.id, usuario_ia.id, status_anterior, lead.status)
    return lead


def obter_todos_leads(db, loja_id: int | None = None) -> list[Lead]:
    q = db.query(Lead)
    if loja_id is not None:
        q = q.filter(Lead.loja_id == loja_id)
    return q.order_by(Lead.atualizado_em.desc()).all()


def obter_lead_por_id(db, lead_id: int) -> Lead | None:
    return db.query(Lead).filter(Lead.id == lead_id).first()


def obter_lead_mais_recente(db, loja_id: int, numero_telefone: str) -> Lead | None:
    return (
        db.query(Lead)
        .filter(Lead.loja_id == loja_id, Lead.numero_telefone == numero_telefone)
        .order_by(Lead.criado_em.desc())
        .first()
    )


def obter_status_lead_mais_recente(db, loja_id: int, numero_telefone: str) -> str | None:
    lead = obter_lead_mais_recente(db, loja_id, numero_telefone)
    return lead.status if lead else None


def definir_status_lead(db, lead: Lead, status: str, usuario_id: int | None = None, observacao: str | None = None) -> Lead:
    """Atualiza o status do lead manualmente (painel admin). Se for um status "fechado" (ver
    STATUS_LEAD_FECHADOS), também encerra a sessão de conversa ativa — main.py usa
    STATUS_LEAD_SILENCIADOS (mais amplo, inclui transferido) pra decidir se o bot responde ou não
    a próxima mensagem. Registra a mudança em lead_historico com quem fez (usuario_id) e por quê."""
    status_anterior = lead.status
    lead.status = status
    lead.atualizado_em = agora_utc()
    db.commit()
    db.refresh(lead)
    if status in STATUS_LEAD_FECHADOS:
        encerrar_conversa(db, lead.numero_telefone, status)
    registrar_mudanca_status(db, lead.id, usuario_id, status_anterior, status, observacao)
    return lead


def criar_lead_apos_encerramento(db, loja_id: int, numero_telefone: str, status_anterior: str) -> Lead:
    """Cria um lead novo (status "novo") quando um cliente cujo lead anterior estava fechado (ver
    STATUS_LEAD_FECHADOS) volta a mandar mensagem — pra alguém da loja revisar manualmente por que
    ele voltou, em vez de reabrir o lead antigo já fechado."""
    lead = Lead(
        loja_id=loja_id,
        numero_telefone=numero_telefone,
        observacoes=f'Cliente retomou contato — lead anterior estava marcado como "{status_anterior}". Revisar manualmente.',
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def lead_para_dict(lead: Lead) -> dict:
    return {
        "id": lead.id,
        "numero_telefone": lead.numero_telefone,
        "nome": lead.nome,
        "email": lead.email,
        "telefone": lead.telefone,
        "veiculo_interesse": lead.veiculo_interesse,
        "veiculo_slug": lead.veiculo_slug,
        "forma_pagamento": lead.forma_pagamento,
        "tem_troca": lead.tem_troca,
        "veiculo_troca_desc": lead.veiculo_troca_desc,
        "orcamento_aproximado": lead.orcamento_aproximado,
        "urgencia_compra": lead.urgencia_compra,
        "uso_pretendido": lead.uso_pretendido,
        "como_conheceu": lead.como_conheceu,
        "preferencia_contato": lead.preferencia_contato,
        "resumo_executivo": lead.resumo_executivo,
        "prioridade": lead.prioridade,
        "observacoes": lead.observacoes,
        "status": lead.status,
        "criado_em": lead.criado_em.isoformat() if lead.criado_em else None,
        "atualizado_em": lead.atualizado_em.isoformat() if lead.atualizado_em else None,
    }


# ── Novidades (posts próprios da loja) ──────────────────────────────────────
def obter_novidades_publicas(db, loja_id: int) -> list:
    return (
        db.query(Novidade)
        .filter(Novidade.loja_id == loja_id, Novidade.publicado.is_(True))
        .order_by(Novidade.criado_em.desc())
        .all()
    )


def obter_todas_novidades(db, loja_id: int) -> list:
    """Sem filtro de publicado — pro admin gerenciar rascunhos também."""
    return (
        db.query(Novidade)
        .filter(Novidade.loja_id == loja_id)
        .order_by(Novidade.criado_em.desc())
        .all()
    )


def obter_novidade_por_slug(db, loja_id: int, slug: str, apenas_publicada: bool = True) -> Novidade | None:
    q = db.query(Novidade).filter(Novidade.loja_id == loja_id, Novidade.slug == slug)
    if apenas_publicada:
        q = q.filter(Novidade.publicado.is_(True))
    return q.first()


def salvar_novidade(db, loja_id: int, data: dict, slug: str | None = None) -> Novidade:
    post = None
    if slug:
        post = obter_novidade_por_slug(db, loja_id, slug, apenas_publicada=False)
    if not post:
        post = Novidade(loja_id=loja_id)
        db.add(post)
    for campo in ("titulo", "slug", "resumo", "conteudo", "url_imagem", "caminho_local_imagem", "publicado"):
        if campo in data:
            setattr(post, campo, data[campo])
    db.commit()
    db.refresh(post)
    return post


def excluir_novidade(db, loja_id: int, slug: str) -> None:
    post = obter_novidade_por_slug(db, loja_id, slug, apenas_publicada=False)
    if post:
        db.delete(post)
        db.commit()


# ── Vídeos do Instagram (Fase 2 — ver plano) ────────────────────────────────
def obter_posts_instagram_visiveis(db, loja_id: int) -> list:
    return (
        db.query(PostInstagram)
        .filter(PostInstagram.loja_id == loja_id, PostInstagram.visivel.is_(True))
        .order_by(PostInstagram.data_hora.desc())
        .all()
    )


def obter_todos_posts_instagram(db, loja_id: int) -> list:
    return (
        db.query(PostInstagram)
        .filter(PostInstagram.loja_id == loja_id)
        .order_by(PostInstagram.data_hora.desc())
        .all()
    )


def salvar_post_instagram(db, loja_id: int, data: dict) -> PostInstagram:
    post = (
        db.query(PostInstagram)
        .filter(PostInstagram.loja_id == loja_id, PostInstagram.id_midia == data["id_midia"])
        .first()
    )
    if not post:
        post = PostInstagram(loja_id=loja_id, id_midia=data["id_midia"])
        db.add(post)
    for campo in ("legenda", "tipo_midia", "url_midia", "url_miniatura", "link_permanente", "data_hora"):
        if campo in data:
            setattr(post, campo, data[campo])
    post.sincronizado_em = agora_utc()
    db.commit()
    db.refresh(post)
    return post


def definir_visibilidade_post_instagram(db, loja_id: int, post_id: int, visivel: bool) -> PostInstagram | None:
    post = (
        db.query(PostInstagram)
        .filter(PostInstagram.loja_id == loja_id, PostInstagram.id == post_id)
        .first()
    )
    if post:
        post.visivel = visivel
        db.commit()
        db.refresh(post)
    return post


# ── Avaliações do Google (Fase 3 — ver plano) ───────────────────────────────
def obter_avaliacoes_google(db, loja_id: int) -> list:
    return (
        db.query(AvaliacaoGoogle)
        .filter(AvaliacaoGoogle.loja_id == loja_id)
        .order_by(AvaliacaoGoogle.id.asc())
        .all()
    )


def substituir_avaliacoes_google(db, loja_id: int, avaliacoes: list) -> None:
    """Substitui tudo a cada sync — mesmo espírito de substituir_imagens_veiculo, é um cache
    simples, não precisa de histórico."""
    db.query(AvaliacaoGoogle).filter(AvaliacaoGoogle.loja_id == loja_id).delete()
    for r in avaliacoes:
        db.add(AvaliacaoGoogle(loja_id=loja_id, **r))
    db.commit()
