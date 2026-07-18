"""Testes de integracao da Fase 2 - Comunicacao (Caps 5, 14).

Cobrem o criterio de "pronto" do PLANO_IMPLEMENTACAO: o Notebook conversa
com o Raspberry e, atraves dele, com o Mega - usando transportes reais
(TCP de loopback e um par de pty como porta serial virtual), nao apenas
FakeTransporte em memoria como em tests/unit. Validado manualmente tambem
contra hardware fisico real (Mega via PlatformIO, Notebook via rede) -
ver docs/journal.md.
"""
import asyncio
import os

import pytest

from orion.communication.discovery import descobrir
from orion.communication.framing import DecodificadorSerial, codificar_serial
from orion.communication.heartbeat import MonitorHeartbeat
from orion.communication.protocol import Mensagem, TipoMensagem
from orion.communication.service import ComunicacaoService
from orion.communication.transport import (
    ConexaoTcp,
    SerialTransport,
    TcpTransport,
    iniciar_servidor_tcp,
)
from orion.kernel.event_bus import EventBus

pytestmark = pytest.mark.sim


@pytest.mark.asyncio
async def test_notebook_conversa_com_raspberry_via_tcp_real():
    """Cliente TCP real (Notebook) <-> servidor TCP real (Raspberry
    simulado) - equivalente ao que tools/sim_raspberry.py expoe."""
    bus_raspberry = EventBus()
    tarefa_bus_raspberry = asyncio.create_task(bus_raspberry.iniciar())
    servico_raspberry = ComunicacaoService("motion_core", bus_raspberry, versao_modulo="sim-0.1.0")

    async def ao_conectar(conexao: ConexaoTcp) -> None:
        servico_raspberry.adicionar_link("mission_core", conexao)

    servidor = await iniciar_servidor_tcp("127.0.0.1", 0, ao_conectar)
    porta = servidor.sockets[0].getsockname()[1]

    bus_notebook = EventBus()
    tarefa_bus_notebook = asyncio.create_task(bus_notebook.iniciar())
    servico_notebook = ComunicacaoService("mission_core", bus_notebook, versao_modulo="test-0.1")
    cliente = TcpTransport("127.0.0.1", porta)
    await cliente.conectar()
    servico_notebook.adicionar_link("motion_core", cliente)

    info = await descobrir(servico_notebook, "motion_core", bus_notebook, timeout_s=2)
    assert info.nome == "motion_core"

    ack = await servico_notebook.send("motion_core", {"acao": "MOVE_TO", "x": 1, "y": 2})
    assert ack.tipo is TipoMensagem.ACK

    await servico_notebook.encerrar()
    await servico_raspberry.encerrar()
    servidor.close()
    await servidor.wait_closed()
    bus_notebook.parar()
    bus_raspberry.parar()
    await tarefa_bus_notebook
    await tarefa_bus_raspberry


@pytest.mark.asyncio
async def test_raspberry_conversa_com_arduino_via_serial_pty():
    """SerialTransport real (Raspberry) <-> ponta mestre de um pty
    respondendo como o firmware (WHO_ARE_YOU/ACK) - equivalente ao que
    tools/sim_arduino.py expoe, mas inline para nao depender de subprocesso."""
    master_fd, escravo_fd = os.openpty()
    caminho_escravo = os.ttyname(escravo_fd)

    async def arduino_simulado() -> None:
        # loop.add_reader (nao run_in_executor+os.read bloqueante) para a
        # task ser cancelavel de verdade: um os.read() bloqueante numa
        # thread do executor nao e interrompido por task.cancel().
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

    tarefa_arduino = asyncio.create_task(arduino_simulado())

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    servico = ComunicacaoService("motion_core", bus, versao_modulo="test-0.1")
    transporte = SerialTransport(
        caminho_escravo, baud_rate=115200, timeout_leitura_s=0.05, atraso_reset_s=0
    )
    await transporte.conectar()
    servico.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)

    info = await descobrir(servico, "hardware_core", bus, timeout_s=2)
    assert info.nome == "hardware_core"

    await servico.encerrar()
    tarefa_arduino.cancel()
    try:
        await tarefa_arduino
    except asyncio.CancelledError:
        pass
    os.close(master_fd)
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_perda_de_heartbeat_gera_module_lost_com_transporte_tcp_real():
    """Fecha a conexao do outro lado (sem avisar) e confere que o
    MonitorHeartbeat detecta a perda mesmo sobre um transporte real."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    perdidos = []
    bus.subscribe("comm.module_lost", lambda e: perdidos.append(e.dados["modulo"]))

    servico = ComunicacaoService("mission_core", bus)
    conexoes: list[ConexaoTcp] = []

    async def ao_conectar(conexao: ConexaoTcp) -> None:
        conexoes.append(conexao)

    servidor = await iniciar_servidor_tcp("127.0.0.1", 0, ao_conectar)
    porta = servidor.sockets[0].getsockname()[1]

    cliente = TcpTransport("127.0.0.1", porta)
    await cliente.conectar()
    servico.adicionar_link("motion_core", cliente)
    await asyncio.sleep(0.05)  # da tempo do servidor aceitar a conexao

    monitor = MonitorHeartbeat(servico, bus, intervalo_s=0.05, heartbeats_perdidos_limite=2)
    monitor.monitorar("motion_core")
    tarefa_monitor = asyncio.create_task(monitor.iniciar())

    await conexoes[0].fechar()  # simula o Raspberry sumindo sem aviso

    await asyncio.sleep(0.5)
    monitor.parar()
    tarefa_monitor.cancel()
    try:
        await tarefa_monitor
    except asyncio.CancelledError:
        pass

    assert "motion_core" in perdidos

    await servico.encerrar()
    servidor.close()
    await servidor.wait_closed()
    bus.parar()
    await tarefa_bus
