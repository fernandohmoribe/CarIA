"""
Conector de estoque para lojas cujo site é feito em Supabase. Lê direto da API
REST (PostgREST) do projeto Supabase da loja, só leitura, sempre filtrando por
veículo disponível e publicado.
"""

from __future__ import annotations

from collections import defaultdict

import httpx

from connectors.base import VehicleSourceConnector

_CHUNK_SIZE = 50


class SupabaseVehicleConnector(VehicleSourceConnector):
    def __init__(self, base_url: str, anon_key: str, only_available: bool = True, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.anon_key = anon_key
        self.only_available = only_available
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"apikey": self.anon_key, "Authorization": f"Bearer {self.anon_key}"}

    def download_image(self, image_url: str, dest_path, width: int = 1000, height: int = 750, quality: int = 78) -> bool:
        """Baixa uma imagem já otimizada (resize + WebP) via a API de transformação do
        Supabase Storage, direto pra dest_path. Retorna True se conseguiu salvar."""
        marker = "/storage/v1/object/public/"
        idx = image_url.find(marker)
        if idx == -1:
            return False
        base = image_url[:idx]
        rest = image_url[idx + len(marker):]
        transform_url = f"{base}/storage/v1/render/image/public/{rest}?width={width}&height={height}&resize=contain&quality={quality}"

        headers = self._headers()
        headers["Accept"] = "image/webp"
        try:
            resp = httpx.get(transform_url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
        except httpx.HTTPError:
            return False

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(resp.content)
        return True

    def fetch_vehicles(self) -> list[dict]:
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
        return [self._normalize_vehicle(v) for v in resp.json()]

    def fetch_images(self, external_ids: list[str]) -> dict[str, list[dict]]:
        images_by_vehicle: dict[str, list[dict]] = defaultdict(list)

        for i in range(0, len(external_ids), _CHUNK_SIZE):
            chunk = external_ids[i : i + _CHUNK_SIZE]
            id_list = ",".join(chunk)
            params = {
                "select": "*",
                "vehicle_id": f"in.({id_list})",
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
                images_by_vehicle[row["vehicle_id"]].append(
                    {
                        "image_url": row["image_url"],
                        "is_cover": bool(row.get("is_cover", False)),
                        "sort_order": row.get("sort_order", 0) or 0,
                    }
                )

        return dict(images_by_vehicle)

    @staticmethod
    def _normalize_vehicle(v: dict) -> dict:
        return {
            "external_id": v["id"],
            "slug": v["slug"],
            "code": v.get("code"),
            "brand": v.get("brand"),
            "model": v.get("model"),
            "version": v.get("version"),
            "year": v.get("year"),
            "price": v.get("price"),
            "mileage": v.get("mileage"),
            "status": v.get("status"),
            "publication_status": v.get("publication_status"),
            "body": v.get("body"),
            "transmission": v.get("transmission"),
            "fuel": v.get("fuel"),
            "color": v.get("color"),
            "spec": v.get("spec"),
            "overview": v.get("overview"),
            "highlights": v.get("highlights") or [],
            "cover_image_url": v.get("cover_image_url"),
        }
