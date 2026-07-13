"""
Sincroniza os vídeos/posts do Instagram da loja pro banco local — puramente pra alimentar
a curadoria manual no admin (`/admin/instagram`), que decide quais aparecem na home do site.

Fase 2 do plano de site novo — depende de credenciais que só o dono da loja consegue gerar:
conta profissional (Business/Creator), app no Meta for Developers com o produto "Instagram
API" e um access token de longa duração (~60 dias, precisa renovar periodicamente).

Sem INSTAGRAM_ACCESS_TOKEN/INSTAGRAM_BUSINESS_ACCOUNT_ID configurados, roda sem erro e não
sincroniza nada — a seção correspondente no site público já fica oculta sozinha (sem posts
marcados como visível).

Uso:
    python sync_instagram.py
"""

import os
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv()

from database import SessionLocal, obter_loja_padrao, salvar_post_instagram

GRAPH_API_BASE = "https://graph.instagram.com"
# Nomes de campo da API do Instagram (contrato externo) — não são nosso código, ficam em inglês.
MEDIA_FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp"


def rodar_sincronizacao() -> int:
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    ig_user_id = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
    if not access_token or not ig_user_id:
        print("INSTAGRAM_ACCESS_TOKEN/INSTAGRAM_BUSINESS_ACCOUNT_ID não configurados — pulando sync do Instagram.")
        return 0

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        if not loja:
            print("Nenhuma loja cadastrada ainda — rode sync_inventory.py primeiro.")
            return 0

        resp = httpx.get(
            f"{GRAPH_API_BASE}/{ig_user_id}/media",
            params={"fields": MEDIA_FIELDS, "access_token": access_token},
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("data", [])

        for item in items:
            data_hora = None
            if item.get("timestamp"):
                # A Graph API devolve o offset sem dois-pontos (+0000), formato que
                # datetime.fromisoformat só passou a aceitar no Python 3.11+.
                data_hora = datetime.strptime(item["timestamp"], "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
            salvar_post_instagram(
                db,
                loja.id,
                {
                    "id_midia": item["id"],
                    "legenda": item.get("caption"),
                    "tipo_midia": item.get("media_type"),
                    "url_midia": item.get("media_url"),
                    "url_miniatura": item.get("thumbnail_url"),
                    "link_permanente": item.get("permalink"),
                    "data_hora": data_hora,
                },
            )
        return len(items)
    finally:
        db.close()


if __name__ == "__main__":
    total = rodar_sincronizacao()
    print(f"Sincronização do Instagram concluída: {total} post(s) importado(s).")
