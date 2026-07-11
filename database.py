from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime

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


class Base(DeclarativeBase):
    pass


# ── Loja (multi-loja pronto, hoje só existe uma linha) ─────────────────────
class Dealership(Base):
    __tablename__ = "dealerships"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    connector_type = Column(String, default="supabase")
    connector_config_json = Column(Text, default="{}")
    staff_phone = Column(String, default="")
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def connector_config(self) -> dict:
        return json.loads(self.connector_config_json or "{}")


# ── Estoque — espelho local, sincronizado pelo sync_inventory.py ──────────
class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (UniqueConstraint("dealership_id", "slug", name="uq_vehicle_dealership_slug"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dealership_id = Column(Integer, ForeignKey("dealerships.id"), index=True)
    external_id = Column(String, index=True)  # id do veículo no sistema de origem
    slug = Column(String, index=True)
    code = Column(String)
    brand = Column(String, index=True)
    model = Column(String, index=True)
    version = Column(String)
    year = Column(Integer)
    price = Column(Float)
    mileage = Column(Integer)
    status = Column(String)
    publication_status = Column(String)
    body = Column(String)
    transmission = Column(String)
    fuel = Column(String)
    color = Column(String)
    spec = Column(String)
    overview = Column(Text)
    highlights_json = Column(Text, default="[]")
    cover_image_url = Column(String)
    synced_at = Column(DateTime, default=datetime.utcnow)

    images = relationship(
        "VehicleImage", back_populates="vehicle", order_by="VehicleImage.sort_order", cascade="all, delete-orphan"
    )

    def highlights(self) -> list:
        try:
            return json.loads(self.highlights_json or "[]")
        except json.JSONDecodeError:
            return []

    @property
    def cover_local_path(self) -> str | None:
        for img in self.images:
            if img.is_cover and img.local_path:
                return img.local_path
        if self.images and self.images[0].local_path:
            return self.images[0].local_path
        return None


class VehicleImage(Base):
    __tablename__ = "vehicle_images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), index=True)
    image_url = Column(String, nullable=False)
    local_path = Column(String, nullable=True)  # caminho relativo dentro de media/, se já baixada
    is_cover = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)

    vehicle = relationship("Vehicle", back_populates="images")


# ── Conversas ────────────────────────────────────────────────────────────
# Status possíveis: active | completed | expired | reset
#
# A sessão em si continua sendo por phone_number (é assim que o WhatsApp funciona — uma thread
# por número). lead_id é só uma marcação de qual lead estava em pauta durante essa sessão —
# importante porque o mesmo telefone pode ter vários leads ao longo do tempo (ver
# create_lead_after_closure em database.py): sem isso, o histórico de conversa de um lead
# reaberto mostraria tudo daquele telefone desde sempre, misturado com leads antigos já fechados.
# Fica nullable porque a 1ª mensagem de uma conversa nova pode chegar antes de existir lead ainda.
class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_number = Column(String, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    status = Column(String, default="active")
    messages_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


# ── Leads ────────────────────────────────────────────────────────────────
# novo e qualificado são calculados pela IA a partir da conversa — não entram no controle manual.
# Os outros 5 dependem de uma ação humana (vendedor ligou, fechou venda, etc) e ficam editáveis
# no painel admin.
#
# SILENCED_LEAD_STATUSES: bot para de responder esse telefone (main.py checa a cada mensagem).
#   - transferido: a IA desistiu e já avisou "vou chamar um vendedor" — fica em silêncio esperando
#     um humano assumir, mas é o MESMO atendimento (conversa não é resetada, sem lead novo).
#   - contatado/convertido/perdido: também fazem parte de CLOSED_LEAD_STATUSES (ver abaixo).
#
# CLOSED_LEAD_STATUSES (subconjunto de SILENCED): o assunto está genuinamente encerrado — além de
#   silenciar, reseta a conversa; se o cliente insistir depois, recebe uma cortesia e um lead novo
#   é criado pra revisão manual, em vez de reabrir o lead antigo.
LEAD_STATUS_LABELS = {
    "novo": "Novo",
    "qualificado": "Qualificado",
    "agendado": "Agendado",
    "transferido": "Transferido",
    "contatado": "Contatado",
    "convertido": "Convertido",
    "perdido": "Perdido",
}
MANUAL_LEAD_STATUSES = ["agendado", "transferido", "contatado", "convertido", "perdido"]
CLOSED_LEAD_STATUSES = {"contatado", "convertido", "perdido"}
SILENCED_LEAD_STATUSES = {"transferido"} | CLOSED_LEAD_STATUSES

# Prioridade: normal | quente
class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dealership_id = Column(Integer, ForeignKey("dealerships.id"), index=True)
    phone_number = Column(String, index=True)
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


# ── Usuários ─────────────────────────────────────────────────────────────
# Cada pessoa tem login próprio (password_hash) — ainda sem diferenciação de permissão entre
# admin/vendedor (ver MELHORIAS), só identidade distinta pra atribuir corretamente no
# lead_historico. password_hash fica nullable porque o usuário especial "IA" (mudanças
# automáticas do bot) nunca loga, só existe pra ser referenciado como autor.
IA_USERNAME = "IA"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    nome = Column(String)
    password_hash = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Histórico de status do lead ─────────────────────────────────────────
class LeadHistorico(Base):
    __tablename__ = "lead_historico"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status_anterior = Column(String)
    status_novo = Column(String)
    observacao = Column(Text, nullable=True)
    data = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


Base.metadata.create_all(bind=engine)


# ── Dealership ───────────────────────────────────────────────────────────
def get_or_create_dealership(db, nome: str, connector_type: str, connector_config: dict, staff_phone: str = "") -> Dealership:
    dealership = db.query(Dealership).filter(Dealership.nome == nome).first()
    if dealership:
        dealership.connector_type = connector_type
        dealership.connector_config_json = json.dumps(connector_config, ensure_ascii=False)
        if staff_phone:
            dealership.staff_phone = staff_phone
        db.commit()
        db.refresh(dealership)
        return dealership

    dealership = Dealership(
        nome=nome,
        connector_type=connector_type,
        connector_config_json=json.dumps(connector_config, ensure_ascii=False),
        staff_phone=staff_phone,
    )
    db.add(dealership)
    db.commit()
    db.refresh(dealership)
    return dealership


def get_default_dealership(db) -> Dealership | None:
    """"Ativa" = a loja mais recente cadastrada. DESC (não ASC) de propósito: ao trocar de
    loja criamos uma linha nova preservando o histórico da antiga (get_or_create_dealership
    busca por nome, então nome diferente = linha nova) — se isso continuasse ordenando por
    id ASC, a loja antiga permaneceria "a" loja resolvida aqui pra sempre."""
    return db.query(Dealership).order_by(Dealership.id.desc()).first()


# ── Usuários ─────────────────────────────────────────────────────────────
_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 com salt aleatório — só stdlib, sem dependência nova pro piloto.
    Formato salvo: "salt_hex$hash_hex"."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, expected_hex = password_hash.split("$", 1)
    except (ValueError, AttributeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), expected_hex)


def get_or_create_user(db, username: str, nome: str | None = None) -> User:
    user = db.query(User).filter(User.username == username).first()
    if user:
        return user
    user = User(username=username, nome=nome or username)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_ia_user(db) -> User:
    return get_or_create_user(db, IA_USERNAME, "Inteligência Artificial")


def create_user_with_password(db, username: str, password: str, nome: str | None = None) -> User:
    """Cria um login novo (ou atualiza a senha de um usuário já existente, ex: o "admin"
    criado via sessão antes de ter senha própria)."""
    user = db.query(User).filter(User.username == username).first()
    if user:
        user.password_hash = hash_password(password)
        if nome:
            user.nome = nome
    else:
        user = User(username=username, nome=nome or username, password_hash=hash_password(password))
        db.add(user)
    db.commit()
    db.refresh(user)
    return user


def verify_user_credentials(db, username: str, password: str) -> bool:
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.password_hash:
        return False
    return verify_password(password, user.password_hash)


# ── Estoque ──────────────────────────────────────────────────────────────
VEHICLE_FIELDS = {
    "external_id", "slug", "code", "brand", "model", "version", "year", "price", "mileage",
    "status", "publication_status", "body", "transmission", "fuel", "color", "spec",
    "overview", "cover_image_url",
}


def upsert_vehicle(db, dealership_id: int, data: dict) -> Vehicle:
    vehicle = (
        db.query(Vehicle)
        .filter(Vehicle.dealership_id == dealership_id, Vehicle.slug == data["slug"])
        .first()
    )
    highlights_json = json.dumps(data.get("highlights", []), ensure_ascii=False)

    if not vehicle:
        vehicle = Vehicle(dealership_id=dealership_id)
        db.add(vehicle)

    for field in VEHICLE_FIELDS:
        if field in data:
            setattr(vehicle, field, data[field])
    vehicle.highlights_json = highlights_json
    vehicle.synced_at = datetime.utcnow()

    db.commit()
    db.refresh(vehicle)
    return vehicle


def replace_vehicle_images(db, vehicle_id: int, images: list) -> None:
    db.query(VehicleImage).filter(VehicleImage.vehicle_id == vehicle_id).delete()
    for img in images:
        db.add(
            VehicleImage(
                vehicle_id=vehicle_id,
                image_url=img["image_url"],
                is_cover=img.get("is_cover", False),
                sort_order=img.get("sort_order", 0),
            )
        )
    db.commit()


def get_available_vehicles(db, dealership_id: int) -> list[Vehicle]:
    return (
        db.query(Vehicle)
        .filter(Vehicle.dealership_id == dealership_id)
        .order_by(Vehicle.brand.asc(), Vehicle.model.asc())
        .all()
    )


def get_vehicle_by_slug(db, dealership_id: int, slug: str) -> Vehicle | None:
    return (
        db.query(Vehicle)
        .filter(Vehicle.dealership_id == dealership_id, Vehicle.slug == slug)
        .first()
    )


# ── Conversas ────────────────────────────────────────────────────────────
def _get_active(db, phone_number: str):
    return (
        db.query(Conversation)
        .filter(Conversation.phone_number == phone_number, Conversation.status == "active")
        .order_by(Conversation.created_at.desc())
        .first()
    )


def _new_session(db, phone_number: str, lead_id: int | None = None) -> "Conversation":
    conv = Conversation(phone_number=phone_number, status="active", lead_id=lead_id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def get_conversation(db, phone_number: str) -> list:
    conv = _get_active(db, phone_number)
    if not conv:
        return []
    return json.loads(conv.messages_json)


def get_conversation_updated_at(db, phone_number: str):
    conv = _get_active(db, phone_number)
    return conv.updated_at if conv else None


def save_conversation(db, phone_number: str, messages: list, lead_id: int | None = None) -> None:
    conv = _get_active(db, phone_number)
    if not conv:
        conv = _new_session(db, phone_number, lead_id)
    conv.messages_json = json.dumps(messages, ensure_ascii=False)
    conv.updated_at = datetime.utcnow()
    if lead_id is not None and conv.lead_id is None:
        # marca com o lead assim que ele existir — a 1ª mensagem de uma conversa pode chegar
        # antes da IA ter chamado criar_ou_atualizar_lead pela primeira vez.
        conv.lead_id = lead_id
    db.commit()


def close_conversation(db, phone_number: str, reason: str = "completed") -> None:
    """Fecha a sessão ativa e abre uma nova vazia (sem lead_id — a próxima save_conversation
    marca com o lead que estiver em pauta nesse momento, que pode ser um lead novo)."""
    conv = _get_active(db, phone_number)
    if conv:
        conv.status = reason
        conv.updated_at = datetime.utcnow()
        db.commit()
    _new_session(db, phone_number)


def get_conversation_history_for_lead(db, lead_id: int) -> list["Conversation"]:
    """Histórico de conversa escopado a UM lead específico — usado no painel, pra não misturar
    sessões de leads antigos já fechados quando o mesmo telefone gera um lead novo."""
    return (
        db.query(Conversation)
        .filter(Conversation.lead_id == lead_id)
        .order_by(Conversation.created_at.desc())
        .all()
    )


# ── Leads ────────────────────────────────────────────────────────────────
_URGENCIA_ALTA_KEYWORDS = ("essa semana", "hoje", "urgente", "o quanto antes", "amanhã", "agora")


def _compute_priority(lead: Lead) -> str:
    urgencia = (lead.urgencia_compra or "").lower()
    is_urgente = any(k in urgencia for k in _URGENCIA_ALTA_KEYWORDS)
    tem_orcamento_ou_pagamento = bool(lead.orcamento_aproximado or lead.forma_pagamento)
    quer_agendar = bool(lead.preferencia_contato)
    if is_urgente and tem_orcamento_ou_pagamento and quer_agendar:
        return "quente"
    return lead.prioridade or "normal"


def get_or_create_lead(db, dealership_id: int, phone_number: str) -> tuple[Lead, bool]:
    """Retorna (lead, is_new) — is_new indica se o registro acabou de ser criado."""
    lead = (
        db.query(Lead)
        .filter(Lead.dealership_id == dealership_id, Lead.phone_number == phone_number)
        .order_by(Lead.created_at.desc())
        .first()
    )
    if lead:
        return lead, False
    lead = Lead(dealership_id=dealership_id, phone_number=phone_number)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead, True


LEAD_UPDATABLE_FIELDS = {
    "nome", "email", "telefone", "veiculo_interesse", "veiculo_slug", "forma_pagamento", "tem_troca",
    "veiculo_troca_desc", "orcamento_aproximado", "urgencia_compra", "uso_pretendido",
    "como_conheceu", "preferencia_contato", "resumo_executivo", "observacoes", "status",
}


def log_status_change(
    db, lead_id: int, user_id: int | None, status_anterior: str | None, status_novo: str, observacao: str | None = None
) -> None:
    """Registra uma linha no histórico só quando o status de fato muda de valor —
    chamadas que atualizam outros campos do lead sem tocar o status não geram entrada."""
    if status_anterior == status_novo:
        return
    db.add(
        LeadHistorico(
            lead_id=lead_id,
            user_id=user_id,
            status_anterior=status_anterior,
            status_novo=status_novo,
            observacao=observacao,
        )
    )
    db.commit()


def get_lead_historico(db, lead_id: int) -> list[LeadHistorico]:
    return (
        db.query(LeadHistorico)
        .filter(LeadHistorico.lead_id == lead_id)
        .order_by(LeadHistorico.data.desc())
        .all()
    )


def update_lead(db, lead: Lead, fields: dict) -> Lead:
    """Atualização feita pela IA via tool `criar_ou_atualizar_lead`. Se o status mudar de
    valor nessa chamada, registra no histórico com o usuário especial "IA"."""
    status_anterior = lead.status
    for key, value in fields.items():
        if key in LEAD_UPDATABLE_FIELDS and value is not None:
            setattr(lead, key, value)
    lead.prioridade = _compute_priority(lead)
    lead.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(lead)
    if lead.status != status_anterior:
        ia_user = get_ia_user(db)
        log_status_change(db, lead.id, ia_user.id, status_anterior, lead.status)
    return lead


def get_all_leads(db, dealership_id: int | None = None) -> list[Lead]:
    q = db.query(Lead)
    if dealership_id is not None:
        q = q.filter(Lead.dealership_id == dealership_id)
    return q.order_by(Lead.updated_at.desc()).all()


def get_lead_by_id(db, lead_id: int) -> Lead | None:
    return db.query(Lead).filter(Lead.id == lead_id).first()


def get_latest_lead(db, dealership_id: int, phone_number: str) -> Lead | None:
    return (
        db.query(Lead)
        .filter(Lead.dealership_id == dealership_id, Lead.phone_number == phone_number)
        .order_by(Lead.created_at.desc())
        .first()
    )


def get_latest_lead_status(db, dealership_id: int, phone_number: str) -> str | None:
    lead = get_latest_lead(db, dealership_id, phone_number)
    return lead.status if lead else None


def set_lead_status(db, lead: Lead, status: str, user_id: int | None = None, observacao: str | None = None) -> Lead:
    """Atualiza o status do lead manualmente (painel admin). Se for um status "fechado" (ver
    CLOSED_LEAD_STATUSES), também encerra a sessão de conversa ativa — main.py usa
    SILENCED_LEAD_STATUSES (mais amplo, inclui transferido) pra decidir se o bot responde ou não
    a próxima mensagem. Registra a mudança em lead_historico com quem fez (user_id) e por quê."""
    status_anterior = lead.status
    lead.status = status
    lead.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(lead)
    if status in CLOSED_LEAD_STATUSES:
        close_conversation(db, lead.phone_number, status)
    log_status_change(db, lead.id, user_id, status_anterior, status, observacao)
    return lead


def create_lead_after_closure(db, dealership_id: int, phone_number: str, previous_status: str) -> Lead:
    """Cria um lead novo (status "novo") quando um cliente cujo lead anterior estava fechado (ver
    CLOSED_LEAD_STATUSES) volta a mandar mensagem — pra alguém da loja revisar manualmente por que
    ele voltou, em vez de reabrir o lead antigo já fechado."""
    lead = Lead(
        dealership_id=dealership_id,
        phone_number=phone_number,
        observacoes=f'Cliente retomou contato — lead anterior estava marcado como "{previous_status}". Revisar manualmente.',
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def lead_to_dict(lead: Lead) -> dict:
    return {
        "id": lead.id,
        "phone_number": lead.phone_number,
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
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
    }
