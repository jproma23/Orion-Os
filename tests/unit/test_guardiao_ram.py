"""Testes do Guardião de RAM do Notebook (EDR-0020; Cap 16)."""
import asyncio

import pytest

from motion_core.behavior.guardiao_ram import GuardiaoRamNotebook
from orion.kernel.event_bus import EventBus


async def _rodar_bus(bus):
    return asyncio.create_task(bus.iniciar())


@pytest.mark.asyncio
async def test_ram_critica_pede_alivio_de_carga():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    pedidos = []
    bus.subscribe("behavior.reduzir_carga_ia", lambda e: pedidos.append(e.dados))

    guardiao = GuardiaoRamNotebook(bus, limiar_critico_mb=700, limiar_folga_mb=1200)

    await bus.publish("diagnostic.notebook_health", {"ram_livre_mb": 500})
    await bus.aguardar_fila_vazia()

    assert guardiao.em_alerta is True
    assert len(pedidos) == 1
    assert pedidos[0]["ram_livre_mb"] == 500

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_histerese_nao_repete_alerta_nem_solta_cedo():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    pedidos = []
    bus.subscribe("behavior.reduzir_carga_ia", lambda e: pedidos.append(e.dados))

    guardiao = GuardiaoRamNotebook(bus, limiar_critico_mb=700, limiar_folga_mb=1200)

    await bus.publish("diagnostic.notebook_health", {"ram_livre_mb": 500})
    await bus.aguardar_fila_vazia()
    # ainda apertado (acima do critico, mas abaixo da folga) -> segue em alerta,
    # sem novo pedido
    await bus.publish("diagnostic.notebook_health", {"ram_livre_mb": 900})
    await bus.aguardar_fila_vazia()
    assert guardiao.em_alerta is True
    assert len(pedidos) == 1

    # folga real voltou -> sai do alerta
    await bus.publish("diagnostic.notebook_health", {"ram_livre_mb": 1500})
    await bus.aguardar_fila_vazia()
    assert guardiao.em_alerta is False

    # caiu de novo -> novo pedido
    await bus.publish("diagnostic.notebook_health", {"ram_livre_mb": 400})
    await bus.aguardar_fila_vazia()
    assert len(pedidos) == 2

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_evento_sem_ram_e_ignorado():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    guardiao = GuardiaoRamNotebook(bus, limiar_critico_mb=700, limiar_folga_mb=1200)

    await bus.publish("diagnostic.notebook_health", {"cpu_percent": 80})
    await bus.aguardar_fila_vazia()
    assert guardiao.em_alerta is False

    bus.parar()
    await tarefa
