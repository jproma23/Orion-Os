"""/velocidade depois de um PARAR (2026-11-10, inter-bloco com parar_tudo).

O `NavigationCore.parar_tudo` publica `navigation.parar` e trava a velocidade
ate um goal novo. A webui respondia `ok: true` ao /velocidade mesmo assim -
a ponte do Nav2 achava que dirigia e o ControleManual contava atividade.
"""

import asyncio
import contextlib

import pytest

pytest.importorskip("aiohttp")
import pytest_asyncio  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from motion_core.webui.server import WebUIServer  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402


@pytest_asyncio.fixture
async def cenario():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    servidor = WebUIServer(bus)
    async with TestClient(TestServer(servidor._app)) as cliente:
        yield cliente, bus
    bus.parar()
    tarefa_bus.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa_bus


@pytest.mark.asyncio
async def test_parar_bloqueia_velocidade_ate_goal_novo(cenario):
    http, bus = cenario
    velocidades = []
    bus.subscribe(
        "navigation.comando",
        lambda e: velocidades.append(e.dados) if e.dados.get("acao") == "VELOCIDADE" else None,
    )

    r = await http.post("/velocidade", json={"v_m_s": 0.05, "w_rad_s": 0.0})
    await bus.aguardar_fila_vazia()
    assert r.status == 200
    assert len(velocidades) == 1

    await bus.publish("navigation.parar", {"origem": "teste"})
    await bus.aguardar_fila_vazia()
    r = await http.post("/velocidade", json={"v_m_s": 0.05, "w_rad_s": 0.0})
    await bus.aguardar_fila_vazia()
    assert r.status == 423
    assert await r.json() == {"ok": False, "motivo": "parado_por_stop"}
    assert len(velocidades) == 1  # nenhum VELOCIDADE depois do PARAR

    r = await http.post("/goal_nav2", json={"x_m": 1.0, "y_m": 0.0})
    await bus.aguardar_fila_vazia()
    assert r.status == 200
    r = await http.post("/velocidade", json={"v_m_s": 0.05, "w_rad_s": 0.0})
    await bus.aguardar_fila_vazia()
    assert r.status == 200
    assert len(velocidades) == 2
