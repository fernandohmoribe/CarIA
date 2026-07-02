"""
Contrato que qualquer fonte de estoque de uma loja precisa implementar.

Cada loja cliente do produto tem seu próprio sistema de origem (hoje: Supabase
da Company Imports). Um conector novo só precisa devolver os dados já no
formato normalizado abaixo — o resto do sistema (sync, banco local, bot) é
genérico e não muda de loja para loja.

Formato normalizado de veículo (dict):
    external_id, slug, code, brand, model, version, year, price, mileage,
    status, publication_status, body, transmission, fuel, color, spec,
    overview, highlights (list[str]), cover_image_url

Formato normalizado de imagem (dict):
    image_url, is_cover (bool), sort_order (int)
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class VehicleSourceConnector(ABC):
    @abstractmethod
    def fetch_vehicles(self) -> list[dict]:
        """Retorna a lista de veículos normalizados (ver formato no módulo)."""
        raise NotImplementedError

    @abstractmethod
    def fetch_images(self, external_ids: list[str]) -> dict[str, list[dict]]:
        """Retorna {external_id_do_veiculo: [imagens normalizadas, ...]}."""
        raise NotImplementedError
