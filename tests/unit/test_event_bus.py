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


# ---------------------------------------------------------------------------
# A7 (auditoria 2026-11-09): CancelledError e BaseException e passava reto
# pelo `except Exception` do despacho - um handler que cancela e aguarda uma
# tarefa propria sem try/except matava o laco do bus, calado, e nenhum evento
# (nem CRITICO) era entregue depois disso.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelled_error_de_um_handler_nao_mata_o_bus():
    bus = EventBus()
    recebidos = []

    async def aguarda_tarefa_cancelada(evento):
        alvo = asyncio.create_task(asyncio.sleep(10))
        await asyncio.sleep(0)
        alvo.cancel()
        await alvo  # padrao comum: cancelar e aguardar sem try/except

    bus.subscribe("x", aguarda_tarefa_cancelada)
    bus.subscribe("y", lambda evento: recebidos.append("y"))
    tarefa = asyncio.create_task(bus.iniciar())
    await bus.publish("x")
    await asyncio.sleep(0.05)
    await bus.publish("y", prioridade=Prioridade.CRITICA)
    await asyncio.sleep(0.05)

    assert not tarefa.done(), "o laco do bus morreu com o CancelledError do handler"
    assert recebidos == ["y"]
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_cancelar_o_proprio_bus_ainda_encerra_o_laco():
    """O outro lado da regra: `task.cancel()` no bus tem que continuar
    funcionando, inclusive no meio de um handler lento."""
    bus = EventBus()
    comecou = asyncio.Event()

    async def lento(evento):
        comecou.set()
        await asyncio.sleep(10)

    bus.subscribe("x", lento)
    tarefa = asyncio.create_task(bus.iniciar())
    await bus.publish("x")
    await comecou.wait()
    tarefa.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarefa
