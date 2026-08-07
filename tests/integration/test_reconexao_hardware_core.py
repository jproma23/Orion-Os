"""Reconexao da serial do Hardware Core (achado de 2026-08-06).

O dono relatou *"quando tira o esp do usb trava tudo"*: a serial so era
aberta no boot e ninguem reconectava. Estes testes cobrem as tres coisas
que fazem o conserto valer:

1. o callback INSISTE ate a porta voltar (uma tentativa so nao serve: o
   caso normal e o cabo ainda estar fora na primeira);
2. duas perdas nao abrem dois lacos resetando o ESP32 em duplicata;
3. o MonitorHeartbeat de fato CHAMA o callback - a maquina de reconexao ja
   existia e ficou orfa por falta de quem passasse essa funcao, que e o
   defeito recorrente deste projeto.

Usa pty de verdade, como o resto de test_motion_core_main.py - nao mocks.
"""
import asyncio
import contextlib
import inspect
import logging
import os

import pytest
from conftest import arduino_simulado

from motion_core import __main__ as motion_main
from motion_core.__main__ import _criar_reconexao_hardware_core
from orion.communication.heartbeat import MonitorHeartbeat
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus

pytestmark = pytest.mark.sim

_LOGGER_TESTE = logging.getLogger("test_reconexao_hardware_core")

PORTA_MORTA = "/dev/ttyDOESNOTEXIST"


@contextlib.asynccontextmanager
async def _bus_no_ar():
    """EventBus rodando, do jeito que o test_motion_core_main.py monta.

    Nao vira fixture async de proposito: o pytest-asyncio deste projeto
    exige `@pytest_asyncio.fixture` para gerador async, e o arquivo vizinho
    ja resolve isso montando inline.
    """
    barramento = EventBus()
    tarefa = asyncio.create_task(barramento.iniciar())
    try:
        yield barramento
    finally:
        barramento.parar()
        tarefa.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarefa


@pytest.mark.asyncio
async def test_reconexao_insiste_ate_a_porta_voltar(monkeypatch):
    """O cabo volta DEPOIS da primeira tentativa - e o robo tem que voltar."""
    # Espera curta: o que se testa e a insistencia, nao o relogio.
    monkeypatch.setattr(motion_main, "RECONEXAO_ESPERA_INICIAL_S", 0.01)
    monkeypatch.setattr(motion_main, "RECONEXAO_ESPERA_MAXIMA_S", 0.02)

    async with _bus_no_ar() as bus:
        comm = ComunicacaoService("motion_core", bus, versao_modulo="test-0.1")
        heartbeat = MonitorHeartbeat(comm, bus, intervalo_s=0.02)

        # Comeca apontando para uma porta que nao existe: e o cabo fora.
        config = {"port": PORTA_MORTA, "baud_rate": 115200}
        ao_perder = _criar_reconexao_hardware_core(
            comm, config, bus, _LOGGER_TESTE, heartbeat, None
        )

        await ao_perder()

        # O cabo volta: a porta passa a existir. `_conectar_hardware_core` le
        # o dicionario a cada tentativa, entao mexer nele E replugar.
        master_fd, escravo_fd = os.openpty()
        tarefa_arduino = asyncio.create_task(arduino_simulado(master_fd))
        await asyncio.sleep(0.05)
        config["port"] = os.ttyname(escravo_fd)

        async with asyncio.timeout(20):
            while "hardware_core" not in comm._links:
                await asyncio.sleep(0.02)

        assert "hardware_core" in comm._links
        # E ficou armado para a PROXIMA queda, senao o conserto vale uma vez so.
        assert heartbeat._reconectar.get("hardware_core") is ao_perder

        await comm.encerrar()
        tarefa_arduino.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarefa_arduino
        os.close(master_fd)


@pytest.mark.asyncio
async def test_reconexao_nao_empilha_dois_lacos(monkeypatch):
    """Duas perdas seguidas nao podem virar dois lacos resetando a placa."""
    monkeypatch.setattr(motion_main, "RECONEXAO_ESPERA_INICIAL_S", 0.05)
    monkeypatch.setattr(motion_main, "RECONEXAO_ESPERA_MAXIMA_S", 0.05)

    async with _bus_no_ar() as bus:
        comm = ComunicacaoService("motion_core", bus, versao_modulo="test-0.1")
        heartbeat = MonitorHeartbeat(comm, bus, intervalo_s=0.02)

        tentativas: list[int] = []
        real = motion_main._conectar_hardware_core

        async def _contando(*args, **kwargs):
            tentativas.append(1)
            return await real(*args, **kwargs)

        monkeypatch.setattr(motion_main, "_conectar_hardware_core", _contando)

        ao_perder = _criar_reconexao_hardware_core(
            comm,
            {"port": PORTA_MORTA, "baud_rate": 115200},
            bus,
            _LOGGER_TESTE,
            heartbeat,
            None,
        )

        await ao_perder()
        await asyncio.sleep(0.01)
        await ao_perder()  # segunda perda enquanto a primeira ainda insiste
        await ao_perder()

        await asyncio.sleep(0.12)

        # Com UM laco a 0,05 s a conta fica em ~3 tentativas; com tres lacos
        # passaria de 6. A folga e generosa de proposito - o que nao pode e
        # multiplicar.
        assert len(tentativas) <= 4, f"lacos empilhados: {len(tentativas)} tentativas"

        await comm.encerrar()


def test_o_boot_passa_o_callback_de_reconexao():
    """A guarda contra a REGRESSAO exata que aconteceu.

    Os testes de cima exercitam a funcao nova, e o de baixo exercita a
    maquina do heartbeat - mas nenhum dos dois falharia no bug original,
    onde as duas pontas existiam e ninguem as ligava. Este falha.

    Le o fonte de `principal()` de proposito: a fiacao acontece dentro de
    uma funcao de boot de ~400 linhas que abre serial, banco e servidor
    web, e nao da para exercita-la num teste sem montar o robo inteiro.
    Se um dia `principal()` for quebrada em pedacos testaveis, ESTE teste
    e o primeiro a ser jogado fora - ele guarda uma linha, nao um
    comportamento.
    """
    fonte = inspect.getsource(motion_main.principal)
    assert "_criar_reconexao_hardware_core" in fonte, (
        "o boot parou de passar o callback de reconexao do Hardware Core - "
        "tirar o ESP32 do USB volta a travar tudo ate reiniciar o servico"
    )


@pytest.mark.asyncio
async def test_heartbeat_chama_o_callback_quando_o_peer_some():
    """A guarda contra o codigo orfao: a perda TEM que acionar o callback.

    Este e o teste que teria pego o defeito original - a maquina de
    reconexao existia, `_marcar_perdido` ja a chamava, e o callback nunca
    era passado.
    """
    async with _bus_no_ar() as bus:
        comm = ComunicacaoService("motion_core", bus, versao_modulo="test-0.1")
        heartbeat = MonitorHeartbeat(comm, bus, intervalo_s=0.02, heartbeats_perdidos_limite=1)

        chamou = asyncio.Event()

        async def _reconectar() -> None:
            chamou.set()

        heartbeat.monitorar("hardware_core", reconectar=_reconectar, heartbeats_perdidos_limite=1)
        # Sem link registrado, cada envio de heartbeat falha - e o mesmo
        # caminho de quando o cabo sai.
        tarefa = asyncio.create_task(heartbeat.iniciar())

        async with asyncio.timeout(20):
            await chamou.wait()

        assert chamou.is_set()

        heartbeat.parar()
        tarefa.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarefa
        await comm.encerrar()
