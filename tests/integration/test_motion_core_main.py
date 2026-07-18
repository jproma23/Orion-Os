"""Testes de integracao do processo principal do Motion Core (Cap 6, 14).

Cobre as decisoes de tolerancia a hardware ausente (Cap 6 s.8): Arduino
nao conectado, SSD nao montado - nenhuma delas pode abortar o processo.
Usa transportes reais (pty serial), mesmo padrao de
test_fase2_comunicacao.py, nao mocks.
"""
import asyncio
import contextlib
import logging
import os

import pytest

from motion_core.__main__ import _abrir_memoria, _conectar_arduino
from orion.communication.framing import DecodificadorSerial, codificar_serial
from orion.communication.protocol import Mensagem, TipoMensagem
from orion.communication.service import ComunicacaoService
from orion.kernel.config import ConfigurationManager
from orion.kernel.event_bus import EventBus

pytestmark = pytest.mark.sim

_LOGGER_TESTE = logging.getLogger("test_motion_core_main")


async def _arduino_simulado(master_fd: int) -> None:
    loop = asyncio.get_running_loop()
    decodificador = DecodificadorSerial()
    fila: asyncio.Queue[bytes] = asyncio.Queue()

    def _ao_ter_dados() -> None:
        try:
            fila.put_nowait(os.read(master_fd, 4096))
        except OSError:
            pass

    loop.add_reader(master_fd, _ao_ter_dados)
    try:
        while True:
            dados = await fila.get()
            for byte in dados:
                quadro = decodificador.alimentar(byte)
                if quadro is None:
                    continue
                pedido = Mensagem.from_bytes(quadro)
                if pedido.tipo is TipoMensagem.COMMAND:
                    ack = Mensagem.ack(pedido, "hardware_core")
                    os.write(master_fd, codificar_serial(ack.to_bytes()))
                    if pedido.payload.get("comando") == "WHO_ARE_YOU":
                        resposta = Mensagem.nova(
                            TipoMensagem.RESPONSE,
                            "hardware_core",
                            pedido.origem,
                            {
                                "nome": "hardware_core",
                                "versao_modulo": "test-sim",
                                "versao_protocolo": "1.0",
                            },
                            id_referencia=pedido.id,
                        )
                        os.write(master_fd, codificar_serial(resposta.to_bytes()))
    finally:
        loop.remove_reader(master_fd)


@pytest.mark.asyncio
async def test_conectar_arduino_com_sucesso_via_pty():
    master_fd, escravo_fd = os.openpty()
    caminho_escravo = os.ttyname(escravo_fd)
    tarefa_arduino = asyncio.create_task(_arduino_simulado(master_fd))

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comm = ComunicacaoService("motion_core", bus, versao_modulo="test-0.1")

    conectado = await _conectar_arduino(
        comm, {"port": caminho_escravo, "baud_rate": 115200}, bus, _LOGGER_TESTE
    )

    assert conectado is True

    await comm.encerrar()
    tarefa_arduino.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa_arduino
    os.close(master_fd)
    bus.parar()
    tarefa_bus.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa_bus


@pytest.mark.asyncio
async def test_conectar_arduino_tolera_porta_inexistente():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comm = ComunicacaoService("motion_core", bus, versao_modulo="test-0.1")

    erros: list = []
    bus.subscribe("diagnostic.error", lambda e: erros.append(e))

    conectado = await _conectar_arduino(
        comm, {"port": "/dev/ttyDOESNOTEXIST", "baud_rate": 115200}, bus, _LOGGER_TESTE
    )
    await bus.aguardar_fila_vazia()

    assert conectado is False
    assert len(erros) == 1
    assert erros[0].dados["modulo"] == "hardware_core"

    bus.parar()
    tarefa_bus.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa_bus


@pytest.mark.asyncio
async def test_conectar_arduino_tolera_falta_de_resposta_who_are_you():
    # pty aberto mas ninguem responde do outro lado - simula um dispositivo
    # serial presente mas que nao fala o protocolo ORION (porta errada).
    master_fd, escravo_fd = os.openpty()
    caminho_escravo = os.ttyname(escravo_fd)

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comm = ComunicacaoService("motion_core", bus, versao_modulo="test-0.1", ack_timeout_ms=100)

    conectado = await _conectar_arduino(
        comm, {"port": caminho_escravo, "baud_rate": 115200}, bus, _LOGGER_TESTE
    )

    assert conectado is False

    await comm.encerrar()
    os.close(master_fd)
    bus.parar()
    tarefa_bus.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa_bus


_CONFIG_BASE = """
system:
  robot_name: "Fofão"
  log_level: "INFO"
  profile: "HOME"
communication:
  raspberry:
    tcp_port: 5757
  arduino:
    baud_rate: 115200
  ack_timeout_ms: 500
  max_retries: 3
  heartbeat_interval_s: 1.0
  heartbeats_lost_threshold: 3
database:
  path: "{caminho_db}"
  backup_dir: "{caminho_backup}"
"""


def test_abrir_memoria_sem_ssd_retorna_none(tmp_path):
    caminho_config = tmp_path / "config.yaml"
    caminho_config.write_text(
        _CONFIG_BASE.format(
            caminho_db="/caminho/que/nao/existe/orion.db",
            caminho_backup="/caminho/que/nao/existe/backups",
        )
    )
    config = ConfigurationManager(caminho_config).carregar()
    bus = EventBus()

    resultado = _abrir_memoria(config, bus, _LOGGER_TESTE)

    assert resultado is None


def test_abrir_memoria_com_ssd_disponivel_retorna_memory_api(tmp_path):
    diretorio_ssd = tmp_path / "ssd" / "orion"
    diretorio_ssd.mkdir(parents=True)
    caminho_config = tmp_path / "config.yaml"
    caminho_config.write_text(
        _CONFIG_BASE.format(
            caminho_db=str(diretorio_ssd / "orion.db"),
            caminho_backup=str(diretorio_ssd / "backups"),
        )
    )
    config = ConfigurationManager(caminho_config).carregar()
    bus = EventBus()

    resultado = _abrir_memoria(config, bus, _LOGGER_TESTE)

    assert resultado is not None
