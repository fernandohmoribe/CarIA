"""
Rate limiter genérico de janela deslizante — compartilhado entre o webhook do WhatsApp
(main.py) e o login do painel admin (admin/routes.py). Fica num módulo próprio porque
main.py importa admin.routes, então admin.routes não pode importar de volta de main.py
(import circular).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

_timestamps: dict[str, deque] = defaultdict(deque)
_blocked: dict[str, float] = {}


def is_rate_limited(key: str, max_requests: int, window_seconds: int, block_seconds: int) -> bool:
    """Retorna True se `key` excedeu `max_requests` dentro de `window_seconds` — nesse caso,
    fica bloqueada por `block_seconds` antes de poder tentar de novo."""
    now = time.time()
    if key in _blocked:
        if now < _blocked[key]:
            return True
        del _blocked[key]

    dq = _timestamps[key]
    while dq and now - dq[0] > window_seconds:
        dq.popleft()
    dq.append(now)

    if len(dq) > max_requests:
        _blocked[key] = now + block_seconds
        return True
    return False
