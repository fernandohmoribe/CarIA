"""
Rate limiter genérico de janela deslizante — compartilhado entre o webhook do WhatsApp
(main.py) e o login do painel admin (admin/routes.py). Fica num módulo próprio porque
main.py importa admin.routes, então admin.routes não pode importar de volta de main.py
(import circular).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

_carimbos_tempo: dict[str, deque] = defaultdict(deque)
_bloqueados: dict[str, float] = {}


def esta_limitado_por_taxa(chave: str, max_requisicoes: int, janela_segundos: int, segundos_bloqueio: int) -> bool:
    """Retorna True se `chave` excedeu `max_requisicoes` dentro de `janela_segundos` — nesse
    caso, fica bloqueada por `segundos_bloqueio` antes de poder tentar de novo."""
    agora = time.time()
    if chave in _bloqueados:
        if agora < _bloqueados[chave]:
            return True
        del _bloqueados[chave]

    dq = _carimbos_tempo[chave]
    while dq and agora - dq[0] > janela_segundos:
        dq.popleft()
    dq.append(agora)

    if len(dq) > max_requisicoes:
        _bloqueados[chave] = agora + segundos_bloqueio
        return True
    return False
