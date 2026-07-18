"""Testes do monitor de heartbeat (Cap 14 s.6)."""
import asyncio

import pytest

from orion.communication.heartbeat import MonitorHeartbeat
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus

from conftest import FakeTransporte


@pytest.mark.asyncio
async def test_heartbeat_enviado_periodicamente():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    servico = ComunicacaoService("mission_core", bus)
    transporte = FakeTransporte()
    servico.adicionar_link("motion_core", transporte)

    monitor = MonitorHeartbeat(servico, bus, intervalo_s=0.05, heartbeats_perdidos_limite=3)
    monitor.monitorar("motion_core")

    tarefa_monitor = asyncio.create_task(monitor.iniciar())
    await asyncio.sleep(0.18)
    monitor.parar()
    tarefa_monitor.cancel()
    try:
        await tarefa_monitor
    except asyncio.CancelledError:
        pass

    assert len(transporte.enviados) >= 2  # deveria ter enviado alguns heartbeats

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_heartbeat_perdido_gera_comm_module_lost():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    perdidos = []
    bus.subscribe("comm.module_lost", lambda e: perdidos.append(e.dados["modulo"]))

    servico = ComunicacaoService("mission_core", bus)
    # link "morto": enviar nao falha, mas nada recebe (simula peer offline)
    transporte = FakeTransporte()
    servico.adicionar_link("motion_core", transporte)

    monitor = MonitorHeartbeat(servico, bus, intervalo_s=0.02, heartbeats_perdidos_limite=2)
    monitor.monitorar("motion_core")

    tarefa_monitor = asyncio.create_task(monitor.iniciar())
    await asyncio.sleep(0.3)
    monitor.parar()
    tarefa_monitor.cancel()
    try:
        await tarefa_monitor
    except asyncio.CancelledError:
        pass
    await bus.aguardar_fila_vazia()

    assert "motion_core" in perdidos

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_heartbeat_recebido_marca_modulo_como_recuperado():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    recuperados = []
    bus.subscribe("comm.module_recovered", lambda e: recuperados.append(e.dados["modulo"]))

    servico = ComunicacaoService("mission_core", bus)
    transporte = FakeTransporte()
    servico.adicionar_link("motion_core", transporte)

    monitor = MonitorHeartbeat(servico, bus, intervalo_s=0.02, heartbeats_perdidos_limite=1)
    monitor.monitorar("motion_core")

    # simula perda manualmente (sem esperar o intervalo real)
    monitor._perdidos_atualmente.add("motion_core")

    from orion.communication.protocol import Mensagem, TipoMensagem

    heartbeat_recebido = Mensagem.nova(TipoMensagem.HEARTBEAT, "motion_core", "mission_core")
    await transporte.injetar(heartbeat_recebido.to_bytes())
    await asyncio.sleep(0.05)
    await bus.aguardar_fila_vazia()

    assert recuperados == ["motion_core"]

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_falha_ao_enviar_heartbeat_tambem_gera_comm_module_lost():
    # achado real (Fase 2/7): a deteccao antiga so olhava heartbeats
    # *recebidos* parando de chegar - um peer que desconectou de vez (ex.:
    # TCP fechado do outro lado) faz enviar_heartbeat() falhar toda vez,
    # mas isso nunca virava comm.module_lost, so um aviso de log repetido
    # pra sempre. Ver docs/journal.md.
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    perdidos = []
    bus.subscribe("comm.module_lost", lambda e: perdidos.append(e.dados["modulo"]))

    servico = ComunicacaoService("mission_core", bus)
    # nunca chama adicionar_link("motion_core", ...) - enviar_heartbeat()
    # vai levantar ErroComunicacao ("sem rota") a cada tentativa, simulando
    # um link que morreu de vez.

    monitor = MonitorHeartbeat(servico, bus, intervalo_s=0.02, heartbeats_perdidos_limite=3)
    monitor.monitorar("motion_core")

    tarefa_monitor = asyncio.create_task(monitor.iniciar())
    await asyncio.sleep(0.05)
    monitor.parar()
    tarefa_monitor.cancel()
    try:
        await tarefa_monitor
    except asyncio.CancelledError:
        pass
    await bus.aguardar_fila_vazia()

    assert perdidos == ["motion_core"]  # so uma vez, nao repetido a cada tentativa

    await servico.encerrar()
    bus.parar()
    await tarefa_bus
