from __future__ import annotations

import json
import os
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


class VehicleImage(Base):
    __tablename__ = "vehicle_images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), index=True)
    image_url = Column(String, nullable=False)
    is_cover = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)

    vehicle = relationship("Vehicle", back_populates="images")


# ── Conversas ────────────────────────────────────────────────────────────
# Status possíveis: active | completed | expired | reset
class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_number = Column(String, index=True)
    status = Column(String, default="active")
    messages_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


# ── Leads ────────────────────────────────────────────────────────────────
# Status possíveis: novo | qualificado | agendado | transferido | contatado | convertido | perdido
# Prioridade: normal | quente
class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dealership_id = Column(Integer, ForeignKey("dealerships.id"), index=True)
    phone_number = Column(String, index=True)
    nome = Column(String)
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
    return db.query(Dealership).order_by(Dealership.id.asc()).first()


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


def _new_session(db, phone_number: str) -> "Conversation":
    conv = Conversation(phone_number=phone_number, status="active")
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


def save_conversation(db, phone_number: str, messages: list) -> None:
    conv = _get_active(db, phone_number)
    if not conv:
        conv = _new_session(db, phone_number)
    conv.messages_json = json.dumps(messages, ensure_ascii=False)
    conv.updated_at = datetime.utcnow()
    db.commit()


def close_conversation(db, phone_number: str, reason: str = "completed") -> None:
    """Fecha a sessão ativa e abre uma nova vazia."""
    conv = _get_active(db, phone_number)
    if conv:
        conv.status = reason
        conv.updated_at = datetime.utcnow()
        db.commit()
    _new_session(db, phone_number)


def get_conversation_history(db, phone_number: str) -> list["Conversation"]:
    return (
        db.query(Conversation)
        .filter(Conversation.phone_number == phone_number)
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
    "nome", "telefone", "veiculo_interesse", "veiculo_slug", "forma_pagamento", "tem_troca",
    "veiculo_troca_desc", "orcamento_aproximado", "urgencia_compra", "uso_pretendido",
    "como_conheceu", "preferencia_contato", "resumo_executivo", "observacoes", "status",
}


def update_lead(db, lead: Lead, fields: dict) -> Lead:
    for key, value in fields.items():
        if key in LEAD_UPDATABLE_FIELDS and value is not None:
            setattr(lead, key, value)
    lead.prioridade = _compute_priority(lead)
    lead.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(lead)
    return lead


def get_all_leads(db, dealership_id: int | None = None) -> list[Lead]:
    q = db.query(Lead)
    if dealership_id is not None:
        q = q.filter(Lead.dealership_id == dealership_id)
    return q.order_by(Lead.updated_at.desc()).all()


def get_lead_by_id(db, lead_id: int) -> Lead | None:
    return db.query(Lead).filter(Lead.id == lead_id).first()


def lead_to_dict(lead: Lead) -> dict:
    return {
        "id": lead.id,
        "phone_number": lead.phone_number,
        "nome": lead.nome,
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
