"""
Conector de estoque para lojas cujo site é feito em Supabase. Lê direto da API
REST (PostgREST) do projeto Supabase da loja, só leitura, sempre filtrando por
veículo disponível e publicado.

Importante: os nomes de campo usados nas chamadas à API (`vehicles`, `vehicle_images`,
`vehicle_id`, `sort_order`, `is_cover` etc) são o schema da loja de origem (Supabase de
terceiro) — não são nosso código, então continuam em inglês exatamente como o projeto
Supabase da loja os expõe. Só o dicionário normalizado que devolvemos pro resto do
sistema usa os nomes novos em português.
"""

from __future__ import annotations

from collections import defaultdict

import httpx

from connectors.base import ConectorFonteVeiculos

_TAMANHO_LOTE = 50


class ConectorSupabase(ConectorFonteVeiculos):
    def __init__(self, base_url: str, anon_key: str, only_available: bool = True, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.anon_key = anon_key
        self.only_available = only_available
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"apikey": self.anon_key, "Authorization": f"Bearer {self.anon_key}"}

    def baixar_imagem(self, url_imagem: str, caminho_destino, width: int = 1000, height: int = 750, quality: int = 78) -> bool:
        """Baixa uma imagem já otimizada (resize + WebP) via a API de transformação do
        Supabase Storage, direto pra caminho_destino. Retorna True se conseguiu salvar."""
        marker = "/storage/v1/object/public/"
        idx = url_imagem.find(marker)
        if idx == -1:
            return False
        base = url_imagem[:idx]
        resto = url_imagem[idx + len(marker):]
        url_transformada = f"{base}/storage/v1/render/image/public/{resto}?width={width}&height={height}&resize=contain&quality={quality}"

        headers = self._headers()
        headers["Accept"] = "image/webp"
        try:
            resp = httpx.get(url_transformada, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
        except httpx.HTTPError:
            return False

        caminho_destino.parent.mkdir(parents=True, exist_ok=True)
        caminho_destino.write_bytes(resp.content)
        return True

    def buscar_veiculos(self) -> list[dict]:
        params = {"select": "*"}
        if self.only_available:
            params["status"] = "eq.Disponivel"
            params["publication_status"] = "eq.Publicado"

        resp = httpx.get(
            f"{self.base_url}/rest/v1/vehicles",
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return [self._normalizar_veiculo(v) for v in resp.json()]

    def buscar_imagens(self, ids_externos: list[str]) -> dict[str, list[dict]]:
        imagens_por_veiculo: dict[str, list[dict]] = defaultdict(list)

        for i in range(0, len(ids_externos), _TAMANHO_LOTE):
            lote = ids_externos[i : i + _TAMANHO_LOTE]
            lista_ids = ",".join(lote)
            params = {
                "select": "*",
                "vehicle_id": f"in.({lista_ids})",
                "order": "vehicle_id.asc,sort_order.asc",
            }
            resp = httpx.get(
                f"{self.base_url}/rest/v1/vehicle_images",
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            for row in resp.json():
                imagens_por_veiculo[row["vehicle_id"]].append(
                    {
                        "url_imagem": row["image_url"],
                        "eh_capa": bool(row.get("is_cover", False)),
                        "ordem": row.get("sort_order", 0) or 0,
                    }
                )

        return dict(imagens_por_veiculo)

    @staticmethod
    def _normalizar_veiculo(v: dict) -> dict:
        return {
            "id_externo": v["id"],
            "slug": v["slug"],
            "codigo": v.get("code"),
            "marca": v.get("brand"),
            "modelo": v.get("model"),
            "versao": v.get("version"),
            "ano": v.get("year"),
            "preco": v.get("price"),
            "quilometragem": v.get("mileage"),
            "status": v.get("status"),
            "status_publicacao": v.get("publication_status"),
            "carroceria": v.get("body"),
            "cambio": v.get("transmission"),
            "combustivel": v.get("fuel"),
            "cor": v.get("color"),
            "especificacao": v.get("spec"),
            "descricao": v.get("overview"),
            "destaques": v.get("highlights") or [],
            "url_imagem_capa": v.get("cover_image_url"),
        }
