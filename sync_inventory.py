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
    Veiculo,
    ImagemVeiculo,
    agora_utc,
    obter_ou_criar_loja,
    substituir_imagens_veiculo,
    salvar_veiculo,
)
from connectors.autocerto_connector import ConectorAutoCerto
from connectors.base import ConectorFonteVeiculos
from connectors.supabase_connector import ConectorSupabase

MEDIA_ROOT = Path(__file__).parent / "media"
MAX_DOWNLOAD_WORKERS = 8


def montar_conector(loja) -> ConectorFonteVeiculos:
    config = loja.config_conector()
    if loja.tipo_conector == "supabase":
        return ConectorSupabase(base_url=config["base_url"], anon_key=config["anon_key"])
    elif loja.tipo_conector == "autocerto":
        return ConectorAutoCerto(site_url=config["site_url"])
    raise ValueError(f"tipo_conector não suportado: {loja.tipo_conector}")


def _baixar_todas_imagens(db, conector, loja_id: int) -> None:
    """Baixa as fotos de todos os veículos da loja em paralelo pra media/.

    Idempotente — pula o que já existe em disco. As requisições de download
    rodam em threads (só I/O de rede); a sessão do banco só é usada na thread
    principal, antes e depois, pra não violar a não-thread-safety do SQLAlchemy.
    """
    if not hasattr(conector, "baixar_imagem"):
        return

    veiculos = db.query(Veiculo).filter(Veiculo.loja_id == loja_id).all()

    pendentes = []  # (imagem_id, url_imagem, caminho_destino)
    ja_local = {}  # imagem_id -> caminho_relativo
    for veiculo in veiculos:
        for img in veiculo.imagens:
            caminho_relativo = Path("vehicles") / veiculo.slug / f"{img.ordem}.webp"
            caminho_destino = MEDIA_ROOT / caminho_relativo
            if caminho_destino.exists():
                ja_local[img.id] = str(caminho_relativo)
            else:
                pendentes.append((img.id, img.url_imagem, caminho_destino, str(caminho_relativo)))

    for imagem_id, caminho_relativo in ja_local.items():
        db.query(ImagemVeiculo).filter(ImagemVeiculo.id == imagem_id).update({"caminho_local": caminho_relativo})
    db.commit()

    if not pendentes:
        return

    print(f"Baixando {len(pendentes)} foto(s) nova(s) em paralelo ({MAX_DOWNLOAD_WORKERS} de cada vez)...")

    def _fazer_download(item):
        imagem_id, url_imagem, caminho_destino, caminho_relativo = item
        ok = conector.baixar_imagem(url_imagem, caminho_destino)
        return imagem_id, (caminho_relativo if ok else None)

    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        futures = [pool.submit(_fazer_download, item) for item in pendentes]
        feitas = 0
        for future in as_completed(futures):
            imagem_id, caminho_relativo = future.result()
            feitas += 1
            if caminho_relativo:
                db.query(ImagemVeiculo).filter(ImagemVeiculo.id == imagem_id).update({"caminho_local": caminho_relativo})
            if feitas % 20 == 0 or feitas == len(pendentes):
                print(f"  {feitas}/{len(pendentes)} processadas")

    db.commit()


def rodar_sincronizacao() -> int:
    db = SessionLocal()
    try:
        tipo_conector = os.getenv("DEALERSHIP_CONNECTOR_TYPE", "supabase")
        if tipo_conector == "supabase":
            config_conector = {
                "base_url": os.getenv("SUPABASE_URL", ""),
                "anon_key": os.getenv("SUPABASE_ANON_KEY", ""),
            }
        elif tipo_conector == "autocerto":
            config_conector = {"site_url": os.getenv("AUTOCERTO_SITE_URL", "")}
        else:
            raise ValueError(f"DEALERSHIP_CONNECTOR_TYPE não suportado: {tipo_conector}")

        loja = obter_ou_criar_loja(
            db,
            nome=os.getenv("DEALERSHIP_NAME", "Minha Loja"),
            tipo_conector=tipo_conector,
            config_conector=config_conector,
            telefone_equipe=os.getenv("DEALERSHIP_STAFF_PHONE", ""),
        )

        conector = montar_conector(loja)
        veiculos = conector.buscar_veiculos()
        ids_externos = [v["id_externo"] for v in veiculos]
        imagens_por_veiculo = conector.buscar_imagens(ids_externos)

        for data in veiculos:
            veiculo = salvar_veiculo(db, loja.id, data)
            substituir_imagens_veiculo(db, veiculo.id, imagens_por_veiculo.get(data["id_externo"], []))

        _baixar_todas_imagens(db, conector, loja.id)

        loja.ultima_sincronizacao = agora_utc()
        db.commit()

        return len(veiculos)
    finally:
        db.close()


if __name__ == "__main__":
    total = rodar_sincronizacao()
    print(f"Sincronização concluída: {total} veículo(s) importado(s) para o banco local.")
