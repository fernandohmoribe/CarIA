"""
Sincroniza o estoque de veículos da loja (fonte externa) para o banco local.

Roda por fora do atendimento em tempo real — o bot nunca consulta o sistema
da loja ao vivo durante a conversa, só o banco local que este script mantém
atualizado.

Uso:
    python sync_inventory.py
"""

import os

from dotenv import load_dotenv

load_dotenv()

from database import (
    SessionLocal,
    get_or_create_dealership,
    replace_vehicle_images,
    upsert_vehicle,
)
from connectors.supabase_connector import SupabaseVehicleConnector


def build_connector(dealership) -> SupabaseVehicleConnector:
    config = dealership.connector_config()
    if dealership.connector_type != "supabase":
        raise ValueError(f"connector_type não suportado: {dealership.connector_type}")
    return SupabaseVehicleConnector(base_url=config["base_url"], anon_key=config["anon_key"])


def run_sync() -> int:
    db = SessionLocal()
    try:
        dealership = get_or_create_dealership(
            db,
            nome=os.getenv("DEALERSHIP_NAME", "Company Imports"),
            connector_type="supabase",
            connector_config={
                "base_url": os.getenv("SUPABASE_URL", ""),
                "anon_key": os.getenv("SUPABASE_ANON_KEY", ""),
            },
            staff_phone=os.getenv("DEALERSHIP_STAFF_PHONE", ""),
        )

        connector = build_connector(dealership)
        vehicles = connector.fetch_vehicles()
        external_ids = [v["external_id"] for v in vehicles]
        images_by_vehicle = connector.fetch_images(external_ids)

        for data in vehicles:
            vehicle = upsert_vehicle(db, dealership.id, data)
            replace_vehicle_images(db, vehicle.id, images_by_vehicle.get(data["external_id"], []))

        from datetime import datetime

        dealership.last_sync_at = datetime.utcnow()
        db.commit()

        return len(vehicles)
    finally:
        db.close()


if __name__ == "__main__":
    total = run_sync()
    print(f"Sincronização concluída: {total} veículo(s) importado(s) para o banco local.")
