"""Testes da ponte de conselho Pi<->Notebook e do gancho no maestro.

O que protegem: a IA é OPCIONAL. Notebook mudo, lento ou respondendo
besteira não pode mudar o comportamento do robô nem travar o maestro.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from motion_core.behavior.behavior_core import BehaviorCore
from motion_core.behavior.comportamento import Comportamento
from orion.mission.conselho_protocolo import AtendenteConselhoIA
from motion_core.behavior.ponte_conselho import PonteConselhoIA
from orion.kernel.event_bus import EventBus


@dataclass
class _ConselhoFalso:
    comportamento: str
    motivo: str = "porque sim"
    aceito: bool = True


class _Base(Comportamento):
    nome = "repouso"
    prioridade = 10

    def quer_rodar(self) -> bool:
        return True

    async def executar(self) -> None:
        while True:
            await asyncio.sleep(0.05)


class _Discricionario(Comportamento):
    nome = "ronda"
    prioridade = 20

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self.pedida = False

    def pedir(self) -> None:
        self.pedida = True
        self._reavaliar()

    def quer_rodar(self) -> bool:
        return self.pedida

    async def executar(self) -> None:
        await asyncio.sleep(0.05)
        self.pedida = False


class _Gatilho(Comportamento):
    nome = "atender"
    prioridade = 80

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self.ativo = False

    def quer_rodar(self) -> bool:
        return self.ativo

    async def executar(self) -> None:
        while True:
            await asyncio.sleep(0.05)


# ----- ponte -----


@pytest.mark.asyncio
async def test_pedido_e_resposta_casam_pelo_id() -> None:
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    AtendenteConselhoIA(bus, lambda ctx, ops: _resposta(_ConselhoFalso("ronda")))
    ponte = PonteConselhoIA(bus, timeout_s=2.0)

    resposta = await ponte.pedir("contexto", ["ronda", "repouso"])
    assert resposta is not None
    assert resposta["comportamento"] == "ronda"

    bus.parar()
    await tarefa


async def _resposta(valor):
    return valor


@pytest.mark.asyncio
async def test_sem_resposta_devolve_none_no_timeout() -> None:
    """Notebook calado: o maestro não pode ficar esperando."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    ponte = PonteConselhoIA(bus, timeout_s=0.15)

    assert await ponte.pedir("contexto", ["ronda"]) is None

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_resposta_atrasada_de_pedido_antigo_e_descartada() -> None:
    """Conselho que chega depois do timeout não pode contaminar o próximo."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    ponte = PonteConselhoIA(bus, timeout_s=0.1)

    await ponte.pedir("ctx", ["ronda"])  # vence
    # resposta de um id que já não existe
    await bus.publish("behavior.conselho", {"id": "id-velho", "comportamento": "ronda"})
    await asyncio.sleep(0.05)
    assert ponte._pendentes == {}  # nada pendurado

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_conselheiro_que_explode_nao_responde() -> None:
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())

    async def _explode(ctx, ops):
        raise RuntimeError("ollama caiu")

    AtendenteConselhoIA(bus, _explode)
    ponte = PonteConselhoIA(bus, timeout_s=0.2)
    assert await ponte.pedir("ctx", ["ronda"]) is None

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_conselho_recusado_nao_e_publicado() -> None:
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    AtendenteConselhoIA(
        bus, lambda ctx, ops: _resposta(_ConselhoFalso("x", aceito=False))
    )
    ponte = PonteConselhoIA(bus, timeout_s=0.2)
    assert await ponte.pedir("ctx", ["ronda"]) is None

    bus.parar()
    await tarefa


# ----- gancho no maestro -----


class _PonteFalsa:
    def __init__(self, resposta: dict | None) -> None:
        self._resposta = resposta
        self.pedidos: list[list[str]] = []

    async def pedir(self, contexto: str, opcoes: list[str]) -> dict | None:
        self.pedidos.append(opcoes)
        return self._resposta


@pytest.mark.asyncio
async def test_ia_e_consultada_na_ociosidade_e_aciona_discricionario() -> None:
    bus = EventBus()
    ponte = _PonteFalsa({"comportamento": "ronda", "motivo": "está quieto"})
    maestro = BehaviorCore(bus, ponte_conselho=ponte, montar_contexto=lambda: "ctx")
    maestro.registrar(_Base(bus))
    ronda = _Discricionario(bus)
    maestro.registrar(ronda, discricionario=True)

    tarefa = asyncio.create_task(maestro.executar())
    await asyncio.sleep(0.3)
    maestro.parar()
    await tarefa

    assert ponte.pedidos, "a IA deveria ter sido consultada na ociosidade"
    assert "ronda" in ponte.pedidos[0]
    assert "repouso" in ponte.pedidos[0]


@pytest.mark.asyncio
async def test_ia_nao_e_consultada_quando_ha_gatilho_no_controle() -> None:
    """Com alguém de gatilho concreto no comando, a regra decide sozinha."""
    bus = EventBus()
    ponte = _PonteFalsa({"comportamento": "ronda"})
    maestro = BehaviorCore(bus, ponte_conselho=ponte, montar_contexto=lambda: "ctx")
    maestro.registrar(_Base(bus))
    maestro.registrar(_Discricionario(bus), discricionario=True)
    gatilho = _Gatilho(bus)
    gatilho.ativo = True
    maestro.registrar(gatilho)

    tarefa = asyncio.create_task(maestro.executar())
    await asyncio.sleep(0.25)
    maestro.parar()
    await tarefa

    assert ponte.pedidos == [], "IA não deveria opinar com gatilho ativo"


@pytest.mark.asyncio
async def test_conselho_invalido_nao_aciona_nada() -> None:
    """Nome que não é discricionário registrado é simplesmente ignorado."""
    bus = EventBus()
    ponte = _PonteFalsa({"comportamento": "vigilancia_obstaculo", "motivo": "x"})
    maestro = BehaviorCore(bus, ponte_conselho=ponte, montar_contexto=lambda: "ctx")
    maestro.registrar(_Base(bus))
    ronda = _Discricionario(bus)
    maestro.registrar(ronda, discricionario=True)

    tarefa = asyncio.create_task(maestro.executar())
    await asyncio.sleep(0.3)
    maestro.parar()
    await tarefa

    assert ronda.pedida is False


@pytest.mark.asyncio
async def test_sem_ponte_o_maestro_funciona_igual() -> None:
    """Sem IA nenhuma o maestro tem que rodar exatamente como antes."""
    bus = EventBus()
    maestro = BehaviorCore(bus)
    maestro.registrar(_Base(bus))

    tarefa = asyncio.create_task(maestro.executar())
    await asyncio.sleep(0.15)
    assert maestro.ativo_nome == "repouso"
    maestro.parar()
    await tarefa
