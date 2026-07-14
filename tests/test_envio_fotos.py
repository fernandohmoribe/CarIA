import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import main


def test_enviar_fotos_veiculo_pausa_entre_envios_mas_nao_antes_do_primeiro():
    """Mandar várias fotos em rajada, sem pausa, é o tipo de padrão que o WhatsApp (conexão
    não-oficial) associa a comportamento automatizado — foi visto acontecer em produção logo
    antes de uma restrição real ser aplicada. enviar_fotos_veiculo precisa pausar entre cada
    envio (mas não atrasar o primeiro)."""
    fotos = {
        "veiculo": "Nivus 2023",
        "fotos": [{"caminho_local": None, "url": f"http://x/{i}.jpg"} for i in range(4)],
    }

    resposta_mock = MagicMock()
    resposta_mock.raise_for_status = MagicMock()
    client_mock = MagicMock()
    client_mock.post = AsyncMock(return_value=resposta_mock)
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=False)

    with patch.object(main.httpx, "AsyncClient", return_value=client_mock), \
         patch.object(main.asyncio, "sleep", new=AsyncMock()) as mock_sleep:
        asyncio.run(main.enviar_fotos_veiculo("5544900000199@c.us", fotos))

    assert client_mock.post.await_count == 4  # as 4 fotos foram enviadas
    assert mock_sleep.await_count == 3  # pausa só entre envios, não antes do primeiro
    mock_sleep.assert_awaited_with(main.SEGUNDOS_ENTRE_FOTOS)
