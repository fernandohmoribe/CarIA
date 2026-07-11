"""
Sincroniza o estoque de veículos da loja (fonte externa) para o banco local.

Roda por fora do atendimento em tempo real — o bot nunca consulta o sistema
da loja ao vivo durante a conversa, só o banco local que este script mantém
atualizado.

Uso:
    python sync_inventory.py
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from database import (
    SessionLocal,
    Vehicle,
    VehicleImage,
    get_or_create_dealership,
    replace_vehicle_images,
    upsert_vehicle,
)
from connectors.autocerto_connector import AutoCertoVehicleConnector
from connectors.base import VehicleSourceConnector
from connectors.supabase_connector import SupabaseVehicleConnector

MEDIA_ROOT = Path(__file__).parent / "media"
MAX_DOWNLOAD_WORKERS = 8


def build_connector(dealership) -> VehicleSourceConnector:
    config = dealership.connector_config()
    if dealership.connector_type == "supabase":
        return SupabaseVehicleConnector(base_url=config["base_url"], anon_key=config["anon_key"])
    elif dealership.connector_type == "autocerto":
        return AutoCertoVehicleConnector(site_url=config["site_url"])
    raise ValueError(f"connector_type não suportado: {dealership.connector_type}")


def _download_all_images(db, connector, dealership_id: int) -> None:
    """Baixa as fotos de todos os veículos da loja em paralelo pra media/.

    Idempotente — pula o que já existe em disco. As requisições de download
    rodam em threads (só I/O de rede); a sessão do banco só é usada na thread
    principal, antes e depois, pra não violar a não-thread-safety do SQLAlchemy.
    """
    if not hasattr(connector, "download_image"):
        return

    vehicles = db.query(Vehicle).filter(Vehicle.dealership_id == dealership_id).all()

    pending = []  # (image_id, image_url, dest_path)
    already_local = {}  # image_id -> rel_path
    for vehicle in vehicles:
        for img in vehicle.images:
            rel_path = Path("vehicles") / vehicle.slug / f"{img.sort_order}.webp"
            dest_path = MEDIA_ROOT / rel_path
            if dest_path.exists():
                already_local[img.id] = str(rel_path)
            else:
                pending.append((img.id, img.image_url, dest_path, str(rel_path)))

    for image_id, rel_path in already_local.items():
        db.query(VehicleImage).filter(VehicleImage.id == image_id).update({"local_path": rel_path})
    db.commit()

    if not pending:
        return

    print(f"Baixando {len(pending)} foto(s) nova(s) em paralelo ({MAX_DOWNLOAD_WORKERS} de cada vez)...")

    def _do_download(item):
        image_id, image_url, dest_path, rel_path = item
        ok = connector.download_image(image_url, dest_path)
        return image_id, (rel_path if ok else None)

    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        futures = [pool.submit(_do_download, item) for item in pending]
        done = 0
        for future in as_completed(futures):
            image_id, rel_path = future.result()
            done += 1
            if rel_path:
                db.query(VehicleImage).filter(VehicleImage.id == image_id).update({"local_path": rel_path})
            if done % 20 == 0 or done == len(pending):
                print(f"  {done}/{len(pending)} processadas")

    db.commit()


def run_sync() -> int:
    db = SessionLocal()
    try:
        connector_type = os.getenv("DEALERSHIP_CONNECTOR_TYPE", "supabase")
        if connector_type == "supabase":
            connector_config = {
                "base_url": os.getenv("SUPABASE_URL", ""),
                "anon_key": os.getenv("SUPABASE_ANON_KEY", ""),
            }
        elif connector_type == "autocerto":
            connector_config = {"site_url": os.getenv("AUTOCERTO_SITE_URL", "")}
        else:
            raise ValueError(f"DEALERSHIP_CONNECTOR_TYPE não suportado: {connector_type}")

        dealership = get_or_create_dealership(
            db,
            nome=os.getenv("DEALERSHIP_NAME", "Minha Loja"),
            connector_type=connector_type,
            connector_config=connector_config,
            staff_phone=os.getenv("DEALERSHIP_STAFF_PHONE", ""),
        )

        connector = build_connector(dealership)
        vehicles = connector.fetch_vehicles()
        external_ids = [v["external_id"] for v in vehicles]
        images_by_vehicle = connector.fetch_images(external_ids)

        for data in vehicles:
            vehicle = upsert_vehicle(db, dealership.id, data)
            replace_vehicle_images(db, vehicle.id, images_by_vehicle.get(data["external_id"], []))

        _download_all_images(db, connector, dealership.id)

        from datetime import datetime

        dealership.last_sync_at = datetime.utcnow()
        db.commit()

        return len(vehicles)
    finally:
        db.close()


if __name__ == "__main__":
    total = run_sync()
    print(f"Sincronização concluída: {total} veículo(s) importado(s) para o banco local.")
