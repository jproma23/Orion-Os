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
    corpo = await resposta.json()
    # Comparar o dicionario INTEIRO por igualdade quebrava a cada campo novo
    # (aconteceu em 07/08, com a marca d'agua). O que este teste garante e
    # que os limites chegam ao navegador - nao a ausencia de vizinhos.
    for chave, valor in CONFIG_FRONTEND.items():
        assert corpo[chave] == valor


@pytest.mark.asyncio
async def test_config_leva_a_identificacao_da_build(cliente):
    """A marca d'agua da tela le daqui - sem estes campos ela some."""
    http, _ = cliente
    corpo = await (await http.get("/config")).json()

    build = corpo["build"]
    assert set(build) == {
        "software",
        "versao",
        "autor",
        "criado_em",
        "atualizado_em",
        "commit",
    }
    assert build["software"] == "ORION OS"


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
    # topico fora de TOPICOS_REPASSADOS nao deve ter handler registrado.
    # (Era vision.person_detected, que passou a ser repassado em 12/11 para os
    # olhos do rosto leve olharem para a pessoa.)
    bus = EventBus()
    AvatarServer(bus, config_frontend=CONFIG_FRONTEND)
    assert "lidar.scan" not in bus._assinantes


# ----- instrumentacao da queda (2026-07-29) -----
#
# O dono relatou "a janela do kiosk mostra desconectado quando ele termina de
# falar". Nao foi possivel diagnosticar porque o servidor engolia o motivo em
# silencio. Estes testes guardam as duas coisas que a instrumentacao trouxe.


class _ClienteQuebrado:
    """Finge um cliente cuja escrita falha, do jeito que um socket morto falha."""

    def __init__(self, erro: BaseException) -> None:
        self._erro = erro
        self.tentativas = 0

    async def write(self, dados: bytes) -> None:
        self.tentativas += 1
        raise self._erro


@pytest.mark.asyncio
async def test_cliente_quebrado_e_descartado_e_registrado(cliente, caplog):
    _, bus = cliente
    servidor = AvatarServer(bus, config_frontend=CONFIG_FRONTEND)
    ruim = _ClienteQuebrado(ConnectionResetError("socket morto"))
    servidor._clientes.add(ruim)

    with caplog.at_level("INFO", logger="orion.display.avatar_server"):
        await bus.publish("voice.status", {"estado": "SPEAKING"})
        await bus.aguardar_fila_vazia()

    assert ruim not in servidor._clientes, "cliente morto tem que sair da lista"
    registrado = [r.getMessage() for r in caplog.records]
    assert any("voice.status" in m and "ConnectionResetError" in m for m in registrado), (
        f"a queda tem que aparecer no log, com o tipo da excecao. Log: {registrado}"
    )


@pytest.mark.asyncio
async def test_um_cliente_ruim_nao_cala_os_outros(cliente):
    """O ganho real da clausula ampla: antes, uma excecao de tipo imprevisto
    subia para fora do handler do Event Bus e o evento nao chegava aos DEMAIS
    clientes. Um navegador com problema silenciava o avatar inteiro."""
    _, bus = cliente
    servidor = AvatarServer(bus, config_frontend=CONFIG_FRONTEND)

    recebidos: list[bytes] = []

    class _ClienteBom:
        async def write(self, dados: bytes) -> None:
            recebidos.append(dados)

    # ValueError NAO esta na dupla (ConnectionResetError, RuntimeError):
    # e exatamente o caso que antes escapava sem log e sem entrega.
    ruim = _ClienteQuebrado(ValueError("tipo imprevisto"))
    bom = _ClienteBom()
    servidor._clientes.add(ruim)
    servidor._clientes.add(bom)

    await bus.publish("voice.status", {"estado": "IDLE"})
    await bus.aguardar_fila_vazia()

    assert len(recebidos) == 1, "o cliente bom tem que receber mesmo com outro quebrado"
    assert ruim not in servidor._clientes
    assert bom in servidor._clientes


@pytest.mark.asyncio
async def test_conta_eventos_enviados(cliente):
    """O contador entra no log da desconexao: 'caiu depois de 42s e 137
    eventos' distingue 'nunca funcionou' de 'funcionou e parou'."""
    _, bus = cliente
    servidor = AvatarServer(bus, config_frontend=CONFIG_FRONTEND)

    class _ClienteBom:
        async def write(self, dados: bytes) -> None:
            pass

    servidor._clientes.add(_ClienteBom())
    assert servidor._eventos_enviados == 0

    await bus.publish("voice.status", {"estado": "IDLE"})
    await bus.publish("voice.status", {"estado": "LISTENING"})
    await bus.aguardar_fila_vazia()

    assert servidor._eventos_enviados == 2
