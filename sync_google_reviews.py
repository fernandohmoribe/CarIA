"""
Sincroniza as avaliações do Google da loja pro banco local (cache simples, substituído a
cada rodada) — exibidas na home do site público.

Fase 3 do plano de site novo — depende de credenciais que só o dono da loja consegue gerar:
projeto no Google Cloud com faturamento ativo, Places API habilitada, API key restrita, e o
Place ID do negócio (via "Place ID Finder" do Google).

Limitação real da API: só devolve até 5 avaliações (as mais relevantes, escolhidas pelo
próprio Google) — não existe endpoint pra puxar todas.

Sem GOOGLE_PLACES_API_KEY/GOOGLE_PLACE_ID configurados, roda sem erro e não sincroniza nada —
a seção correspondente no site público já fica oculta sozinha (sem reviews em cache).

Uso:
    python sync_google_reviews.py
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

from database import SessionLocal, obter_loja_padrao, substituir_avaliacoes_google

PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"


def rodar_sincronizacao() -> int:
    api_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
    place_id = os.getenv("GOOGLE_PLACE_ID", "")
    if not api_key or not place_id:
        print("GOOGLE_PLACES_API_KEY/GOOGLE_PLACE_ID não configurados — pulando sync do Google Reviews.")
        return 0

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
        if not loja:
            print("Nenhuma loja cadastrada ainda — rode sync_inventory.py primeiro.")
            return 0

        resp = httpx.get(
            PLACE_DETAILS_URL,
            params={
                "place_id": place_id,
                # Nomes de campo da API do Google (contrato externo) — ficam em inglês.
                "fields": "rating,user_ratings_total,reviews",
                "key": api_key,
                "language": "pt-BR",
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json().get("result", {})
        avaliacoes_api = result.get("reviews", [])

        avaliacoes = [
            {
                "nome_autor": r.get("author_name", ""),
                "url_foto_perfil": r.get("profile_photo_url"),
                "nota": r.get("rating", 0),
                "texto": r.get("text", ""),
                "tempo_relativo": r.get("relative_time_description", ""),
            }
            for r in avaliacoes_api
        ]
        substituir_avaliacoes_google(db, loja.id, avaliacoes)
        return len(avaliacoes)
    finally:
        db.close()


if __name__ == "__main__":
    total = rodar_sincronizacao()
    print(f"Sincronização de avaliações do Google concluída: {total} avaliação(ões) importada(s).")
