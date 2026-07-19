"""Testes do maestro (BehaviorCore, EDR-0020): arbitragem por prioridade,
preempção e retomada. Sem hardware - comportamentos falsos controláveis.
"""
import asyncio

import pytest

from motion_core.behavior.behavior_core import BehaviorCore
from motion_core.behavior.comportamento import Comportamento
from orion.kernel.event_bus import EventBus


class ComportamentoFalso(Comportamento):
    def __init__(self, bus, nome, prioridade):
        super().__init__(bus)
        self.nome = nome
        self.prioridade = prioridade
        self.quer = False
        self.execucoes = 0

    def quer_rodar(self) -> bool:
        return self.quer

    async def executar(self) -> None:
        self.execucoes += 1
        while True:  # segura o controle até ser preemptado
            await asyncio.sleep(0.02)


async def _passo(maestro):
    """Acorda o maestro e dá tempo de uma reavaliação acontecer."""
    maestro.pedir_reavaliacao()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_mais_forte_preempta_e_o_mais_fraco_retoma():
    bus = EventBus()
    maestro = BehaviorCore(bus)
    baixo = ComportamentoFalso(bus, "patrulha", 40)
    alto = ComportamentoFalso(bus, "atender", 80)
    maestro.registrar(baixo)
    maestro.registrar(alto)

    laco = asyncio.create_task(maestro.executar())

    # só a patrulha quer rodar -> ela assume
    baixo.quer = True
    await _passo(maestro)
    assert maestro.ativo_nome == "patrulha"
    assert baixo.execucoes == 1

    # o "atender" (mais forte) quer rodar -> preempta a patrulha
    alto.quer = True
    await _passo(maestro)
    assert maestro.ativo_nome == "atender"

    # atender terminou -> a patrulha retoma (executar chamada de novo)
    alto.quer = False
    await _passo(maestro)
    assert maestro.ativo_nome == "patrulha"
    assert baixo.execucoes == 2

    maestro.parar()
    await laco


@pytest.mark.asyncio
async def test_ninguem_quer_rodar_fica_ocioso():
    bus = EventBus()
    maestro = BehaviorCore(bus)
    c = ComportamentoFalso(bus, "repouso", 10)
    maestro.registrar(c)

    laco = asyncio.create_task(maestro.executar())
    await _passo(maestro)
    assert maestro.ativo_nome is None  # ninguém quer -> nenhum ativo

    maestro.parar()
    await laco


@pytest.mark.asyncio
async def test_desempate_pega_o_de_maior_prioridade():
    bus = EventBus()
    maestro = BehaviorCore(bus)
    a = ComportamentoFalso(bus, "vigia", 60)
    b = ComportamentoFalso(bus, "patrulha", 40)
    maestro.registrar(b)  # registra fora de ordem de propósito
    maestro.registrar(a)

    laco = asyncio.create_task(maestro.executar())
    a.quer = True
    b.quer = True
    await _passo(maestro)
    assert maestro.ativo_nome == "vigia"  # 60 > 40

    maestro.parar()
    await laco
