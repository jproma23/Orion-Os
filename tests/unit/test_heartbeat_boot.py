"""Regressão: o boot não pode reportar perda de quem nunca conectou.

Bug real (2026-07-19): o laço de heartbeat começa antes de o supervisor TCP
abrir o link. `enviar_heartbeat` falhava com "sem rota" e isso era tratado
como PERDA - saía um "Heartbeat perdido: modulo='motion_core'" em toda
partida, publicando comm.module_lost e disparando reconexão para um link
que já estava sendo aberto.

"Ainda não conectei" e "perdi a conexão" são estados diferentes.
"""
from __future__ import annotations

import asyncio

import pytest

from orion.communication.heartbeat import MonitorHeartbeat
from orion.communication.service import ComunicacaoService
from orion.communication.transport import ErroTransporte
from orion.kernel.event_bus import EventBus


class _TransporteOk:
    conectado = True

    async def enviar(self, dados: bytes) -> None:
        pass

    async def receber(self):
        await asyncio.sleep(3600)
        yield b""

    async def fechar(self) -> None:
        self.conectado = False


async def _rodar_monitor(monitor: MonitorHeartbeat, segundos: float) -> None:
    tarefa = asyncio.create_task(monitor.iniciar())
    await asyncio.sleep(segundos)
    monitor.parar()
    tarefa.cancel()
    try:
        await tarefa
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_peer_que_nunca_conectou_nao_vira_module_lost() -> None:
    """O caso do boot: link ainda não aberto, monitor já rodando."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    perdidos: list[str] = []
    bus.subscribe("comm.module_lost", lambda e: perdidos.append(e.dados["modulo"]))

    servico = ComunicacaoService("mission_core", bus)
    monitor = MonitorHeartbeat(servico, bus, intervalo_s=0.02, heartbeats_perdidos_limite=3)
    monitor.monitorar("motion_core")  # sem adicionar_link: link não existe ainda

    await _rodar_monitor(monitor, 0.1)
    await bus.aguardar_fila_vazia()

    assert perdidos == [], "reportou perda de um link que nunca existiu"

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_nao_tenta_reconectar_no_boot() -> None:
    """A reconexão dispararia sobre um link que já estava sendo aberto."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    tentativas: list[int] = []

    async def _reconectar() -> None:
        tentativas.append(1)

    servico = ComunicacaoService("mission_core", bus)
    monitor = MonitorHeartbeat(servico, bus, intervalo_s=0.02, heartbeats_perdidos_limite=3)
    monitor.monitorar("motion_core", reconectar=_reconectar)

    await _rodar_monitor(monitor, 0.1)

    assert tentativas == []

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_depois_de_conectar_a_perda_volta_a_ser_reportada() -> None:
    """A carência é só até o primeiro sucesso - não pode virar mordaça."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    perdidos: list[str] = []
    bus.subscribe("comm.module_lost", lambda e: perdidos.append(e.dados["modulo"]))

    servico = ComunicacaoService("mission_core", bus)

    class _MorreDepoisDoPrimeiro:
        conectado = True

        def __init__(self) -> None:
            self.envios = 0

        async def enviar(self, dados: bytes) -> None:
            self.envios += 1
            if self.envios > 1:
                raise ErroTransporte("caiu")

        async def receber(self):
            await asyncio.sleep(3600)
            yield b""

        async def fechar(self) -> None:
            self.conectado = False

    servico.adicionar_link("motion_core", _MorreDepoisDoPrimeiro())
    monitor = MonitorHeartbeat(servico, bus, intervalo_s=0.02, heartbeats_perdidos_limite=3)
    monitor.monitorar("motion_core")

    await _rodar_monitor(monitor, 0.1)
    await bus.aguardar_fila_vazia()

    assert perdidos == ["motion_core"], "perda real deixou de ser reportada"

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_heartbeat_recebido_tambem_estabelece_o_peer() -> None:
    """Estabelecer não depende só de enviar: receber também conta.

    Importa para o lado que só escuta - um peer de quem já ouvimos
    heartbeat está estabelecido, mesmo que nunca tenhamos enviado com
    sucesso.
    """
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())

    servico = ComunicacaoService("motion_core", bus)
    monitor = MonitorHeartbeat(servico, bus, intervalo_s=0.02, heartbeats_perdidos_limite=3)
    monitor.monitorar("mission_core")

    await bus.publish("comm.mensagem.heartbeat", {"origem": "mission_core"})
    await bus.aguardar_fila_vazia()

    assert "mission_core" in monitor._ja_estabelecidos

    bus.parar()
    await tarefa_bus
