"""Testes do Event Bus (Cap 6 secao 5)."""
import asyncio

import pytest

from orion.kernel.event_bus import EventBus, Prioridade


@pytest.mark.asyncio
async def test_publish_entrega_ao_assinante():
    bus = EventBus()
    recebidos = []
    bus.subscribe("system.ready", lambda evento: recebidos.append(evento.dados))

    tarefa = asyncio.create_task(bus.iniciar())
    await bus.publish("system.ready", {"ok": True})
    await bus.aguardar_fila_vazia()
    bus.parar()
    await tarefa

    assert recebidos == [{"ok": True}]


@pytest.mark.asyncio
async def test_assinante_de_outro_topico_nao_recebe():
    bus = EventBus()
    recebidos = []
    bus.subscribe("vision.person_detected", lambda evento: recebidos.append(evento))

    tarefa = asyncio.create_task(bus.iniciar())
    await bus.publish("system.ready", {})
    await bus.aguardar_fila_vazia()
    bus.parar()
    await tarefa

    assert recebidos == []


@pytest.mark.asyncio
async def test_prioridade_alta_e_despachada_antes():
    bus = EventBus()
    ordem = []
    bus.subscribe("topico", lambda evento: ordem.append(evento.dados["id"]))

    # publica varios eventos de prioridade baixa primeiro, depois um critico -
    # o critico deve ser despachado antes dos que ja estavam na fila, pois o
    # dispatcher ainda nao rodou (bus.iniciar so comeca depois).
    await bus.publish("topico", {"id": "baixa-1"}, prioridade=Prioridade.BAIXA)
    await bus.publish("topico", {"id": "baixa-2"}, prioridade=Prioridade.BAIXA)
    await bus.publish("topico", {"id": "critica"}, prioridade=Prioridade.CRITICA)

    tarefa = asyncio.create_task(bus.iniciar())
    await bus.aguardar_fila_vazia()
    bus.parar()
    await tarefa

    assert ordem == ["critica", "baixa-1", "baixa-2"]


@pytest.mark.asyncio
async def test_handler_com_erro_nao_derruba_o_bus():
    bus = EventBus()
    recebidos = []

    def handler_com_erro(evento):
        raise RuntimeError("falha proposital")

    bus.subscribe("topico", handler_com_erro)
    bus.subscribe("topico", lambda evento: recebidos.append(evento.dados))

    tarefa = asyncio.create_task(bus.iniciar())
    await bus.publish("topico", {"chegou": True})
    await bus.aguardar_fila_vazia()
    bus.parar()
    await tarefa

    assert recebidos == [{"chegou": True}]


@pytest.mark.asyncio
async def test_unsubscribe_remove_handler():
    bus = EventBus()
    recebidos = []

    def handler(evento):
        recebidos.append(evento)

    bus.subscribe("topico", handler)
    bus.unsubscribe("topico", handler)

    tarefa = asyncio.create_task(bus.iniciar())
    await bus.publish("topico", {})
    await bus.aguardar_fila_vazia()
    bus.parar()
    await tarefa

    assert recebidos == []
