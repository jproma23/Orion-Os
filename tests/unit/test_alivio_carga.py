"""Testes do alívio de carga do Notebook.

Contexto: o Guardião de RAM (no Pi) já publicava `behavior.reduzir_carga_ia`
desde que foi escrito, mas NINGUÉM escutava - o evento morria no barramento
e a proteção contra travamento por falta de memória existia só no papel.
Publicar um evento não falha, então o buraco passou despercebido.

Estes testes fixam a outra ponta: alguém atende, sacrifica o que dá para
sacrificar, e devolve tudo quando a folga volta.
"""
from __future__ import annotations

import pytest

from orion.kernel.event_bus import EventBus, Evento
from orion.mission.alivio_carga import (
    TOPICO_PEDIDO_ALIVIO,
    TOPICO_RECUPERADO,
    AlivioCarga,
)


class _Espiao:
    def __init__(self) -> None:
        self.descarregou = 0
        self.pausou = 0
        self.retomou = 0

    async def descarregar(self) -> None:
        self.descarregou += 1

    def pausar(self) -> None:
        self.pausou += 1

    def retomar(self) -> None:
        self.retomou += 1


def _montar(espiao: _Espiao) -> AlivioCarga:
    return AlivioCarga(
        EventBus(),
        descarregar_modelo=espiao.descarregar,
        pausar_visao=espiao.pausar,
        retomar_visao=espiao.retomar,
    )


def _pedido(ram_mb: int = 500) -> Evento:
    return Evento(TOPICO_PEDIDO_ALIVIO, {"ram_livre_mb": ram_mb, "motivo": "ram_critica"})


def _recuperado(ram_mb: int = 1500) -> Evento:
    return Evento(TOPICO_RECUPERADO, {"origem": "guardiao_ram", "ram_livre_mb": ram_mb})


@pytest.mark.asyncio
async def test_pedido_descarrega_modelo_e_pausa_visao() -> None:
    espiao = _Espiao()
    alivio = _montar(espiao)

    await alivio._ao_pedir_alivio(_pedido())

    assert espiao.descarregou == 1
    assert espiao.pausou == 1
    assert alivio.aliviado is True


@pytest.mark.asyncio
async def test_pedido_repetido_nao_alivia_duas_vezes() -> None:
    """O evento pode chegar repetido pelo link - aliviar de novo não ajuda."""
    espiao = _Espiao()
    alivio = _montar(espiao)

    await alivio._ao_pedir_alivio(_pedido())
    await alivio._ao_pedir_alivio(_pedido())

    assert espiao.descarregou == 1
    assert espiao.pausou == 1


@pytest.mark.asyncio
async def test_recuperacao_retoma_a_visao() -> None:
    espiao = _Espiao()
    alivio = _montar(espiao)

    await alivio._ao_pedir_alivio(_pedido())
    await alivio._ao_recuperar(_recuperado())

    assert espiao.retomou == 1
    assert alivio.aliviado is False


@pytest.mark.asyncio
async def test_recuperacao_de_outra_origem_e_ignorada() -> None:
    """`diagnostic.recuperado` é usado por outros módulos também."""
    espiao = _Espiao()
    alivio = _montar(espiao)
    await alivio._ao_pedir_alivio(_pedido())

    await alivio._ao_recuperar(Evento(TOPICO_RECUPERADO, {"origem": "outro_qualquer"}))

    assert espiao.retomou == 0
    assert alivio.aliviado is True, "não podia sair do alívio por evento alheio"


@pytest.mark.asyncio
async def test_recuperacao_sem_alivio_previo_nao_faz_nada() -> None:
    espiao = _Espiao()
    alivio = _montar(espiao)

    await alivio._ao_recuperar(_recuperado())

    assert espiao.retomou == 0


@pytest.mark.asyncio
async def test_falha_ao_descarregar_nao_impede_pausar_a_visao() -> None:
    """Em memória crítica, alívio parcial é melhor que nenhum."""
    espiao = _Espiao()

    async def _explode() -> None:
        raise RuntimeError("ollama fora do ar")

    alivio = AlivioCarga(
        EventBus(),
        descarregar_modelo=_explode,
        pausar_visao=espiao.pausar,
        retomar_visao=espiao.retomar,
    )

    await alivio._ao_pedir_alivio(_pedido())

    assert espiao.pausou == 1, "a visão tinha que pausar mesmo com o modelo falhando"
    assert alivio.aliviado is True


@pytest.mark.asyncio
async def test_funciona_sem_visao_configurada() -> None:
    """Visão desabilitada não pode quebrar o alívio de carga."""
    espiao = _Espiao()
    alivio = AlivioCarga(EventBus(), descarregar_modelo=espiao.descarregar)

    await alivio._ao_pedir_alivio(_pedido())
    await alivio._ao_recuperar(_recuperado())

    assert espiao.descarregou == 1
    assert alivio.aliviado is False


@pytest.mark.asyncio
async def test_ciclo_completo_pode_se_repetir() -> None:
    """RAM some, volta, some de novo - o alívio tem que reagir toda vez."""
    espiao = _Espiao()
    alivio = _montar(espiao)

    await alivio._ao_pedir_alivio(_pedido())
    await alivio._ao_recuperar(_recuperado())
    await alivio._ao_pedir_alivio(_pedido())

    assert espiao.descarregou == 2
    assert espiao.pausou == 2
    assert espiao.retomou == 1


@pytest.mark.asyncio
async def test_assina_os_topicos_no_barramento() -> None:
    """Sem assinatura, o evento volta a morrer no vazio - que era o bug."""
    bus = EventBus()
    espiao = _Espiao()
    AlivioCarga(bus, descarregar_modelo=espiao.descarregar, pausar_visao=espiao.pausar)

    tarefa = None
    import asyncio

    tarefa = asyncio.create_task(bus.iniciar())
    await bus.publish(TOPICO_PEDIDO_ALIVIO, {"ram_livre_mb": 400})
    await bus.aguardar_fila_vazia()

    assert espiao.descarregou == 1
    assert espiao.pausou == 1

    bus.parar()
    await tarefa
