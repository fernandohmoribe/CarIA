"""
Contrato que qualquer fonte de estoque de uma loja precisa implementar.

Cada loja cliente do produto tem seu próprio sistema de origem (ex: Supabase,
AutoCerto). Um conector novo só precisa devolver os dados já no
formato normalizado abaixo — o resto do sistema (sync, banco local, bot) é
genérico e não muda de loja para loja.

Formato normalizado de veículo (dict):
    id_externo, slug, codigo, marca, modelo, versao, ano, preco, quilometragem,
    status, status_publicacao, carroceria, cambio, combustivel, cor, especificacao,
    descricao, destaques (list[str]), url_imagem_capa

Formato normalizado de imagem (dict):
    url_imagem, eh_capa (bool), ordem (int)
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ConectorFonteVeiculos(ABC):
    @abstractmethod
    def buscar_veiculos(self) -> list[dict]:
        """Retorna a lista de veículos normalizados (ver formato no módulo)."""
        raise NotImplementedError

    @abstractmethod
    def buscar_imagens(self, ids_externos: list[str]) -> dict[str, list[dict]]:
        """Retorna {id_externo_do_veiculo: [imagens normalizadas, ...]}."""
        raise NotImplementedError
