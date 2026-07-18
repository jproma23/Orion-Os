"""Testes do servidor do avatar (Cap 13 s.3, 7).

Pula graciosamente onde `aiohttp` nao esta instalado (ex.: neste Raspberry
Pi - Display roda so no Notebook, mesmo padrao de Vision/Voice, EDR-0018).
"""
import asyncio
import contextlib

import pytest

aiohttp = pytest.importorskip("aiohttp")
import pytest_asyncio  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from orion.display.avatar_server import AvatarServer  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402

CONFIG_FRONTEND = {"pan_limits_degrees": [-80, 80], "tilt_limits_degrees": [-30, 45]}


@pytest_asyncio.fixture
async def cliente():
    bus = EventBus()
    # publish() so enfileira - precisa do loop de despacho rodando pra
    # entregar de fato aos assinantes (mesmo padrao usado em producao).
    tarefa_bus = asyncio.create_task(bus.iniciar())
    servidor = AvatarServer(bus, config_frontend=CONFIG_FRONTEND)
    async with TestClient(TestServer(servidor._app)) as cliente:
        yield cliente, bus
    bus.parar()
    tarefa_bus.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa_bus


@pytest.mark.asyncio
async def test_index_serve_o_html_do_avatar(cliente):
    http, _ = cliente
    resposta = await http.get("/")
    assert resposta.status == 200
    assert "text/html" in resposta.headers["Content-Type"]


@pytest.mark.asyncio
async def test_config_expoe_os_limites_de_pan_tilt(cliente):
    http, _ = cliente
    resposta = await http.get("/config")
    assert resposta.status == 200
    assert await resposta.json() == CONFIG_FRONTEND


@pytest.mark.asyncio
async def test_evento_do_bus_e_repassado_para_o_cliente_sse(cliente):
    http, bus = cliente
    async with http.get("/eventos") as resposta:
        assert resposta.status == 200
        assert resposta.headers["Content-Type"] == "text/event-stream"

        await bus.publish("voice.status", {"estado": "SPEAKING"})

        linha = await resposta.content.readline()
        assert linha == b"event: voice.status\n"
        linha_dados = await resposta.content.readline()
        assert linha_dados == b'data: {"estado": "SPEAKING"}\n'


@pytest.mark.asyncio
async def test_topico_nao_repassado_e_ignorado():
    # topico fora de TOPICOS_REPASSADOS nao deve ter handler registrado
    bus = EventBus()
    AvatarServer(bus, config_frontend=CONFIG_FRONTEND)
    assert "vision.person_detected" not in bus._assinantes
