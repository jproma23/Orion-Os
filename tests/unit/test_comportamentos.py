"""Testes dos comportamentos concretos plugados no maestro (EDR-0020)."""
import asyncio

import pytest

from motion_core.behavior.behavior_core import BehaviorCore
from motion_core.behavior.comportamentos import (
    Atender,
    Repouso,
    Vigilia,
    VigilanciaObstaculo,
)
from orion.kernel.event_bus import EventBus


async def _passo(maestro):
    maestro.pedir_reavaliacao()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_repouso_assume_quando_ninguem_mais_quer():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"  # sem obstáculo -> base assume

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_obstaculo_preempta_o_repouso_e_libera_ao_sair():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    vigilancia = VigilanciaObstaculo(bus)
    maestro.registrar(vigilancia)
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"

    # Mega reporta obstáculo -> vigilância (prio 100) assume
    await bus.publish("motion.status", {"estado": "OBSTACLE_DETECTED"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "vigilancia_obstaculo"

    # obstáculo liberado -> volta ao repouso
    await bus.publish("motion.status", {"estado": "IDLE"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "repouso"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_atender_preempta_repouso_e_manda_hold():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos_nav = []
    bus.subscribe("navigation.comando", lambda e: comandos_nav.append(e.dados))

    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(Atender(bus))
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"

    # dono chamou "Fofão" (evento vindo do Notebook)
    await bus.publish("voice.wake_detected", {"texto_janela": "fofão"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "atender"
    assert {"acao": "HOLD"} in comandos_nav  # parou o robô ao atender

    # resposta terminou -> volta ao repouso
    await bus.publish("voice.response_finished", {"texto": "pronto"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "repouso"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_obstaculo_vence_atender():
    """Segurança (100) preempta até o Atender (80): se surge obstáculo
    enquanto atende, a vigilância assume."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(Atender(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    await bus.publish("voice.wake_detected", {})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "atender"

    await bus.publish("motion.status", {"estado": "OBSTACLE_DETECTED"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "vigilancia_obstaculo"  # 100 > 80

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_vigilia_investiga_e_libera_e_perde_para_atender():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    fotos = []
    bus.subscribe("sentinela.capturar_foto", lambda e: fotos.append(e.dados))

    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(Atender(bus))
    maestro.registrar(Vigilia(bus, duracao_alerta_s=0.15))
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"

    # alerta Sentinela -> vigília assume, pede foto
    await bus.publish("sentinela.alerta", {"tipo": "barulho"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "vigilia"
    assert fotos and fotos[0]["motivo"] == "barulho"

    # dono chama "Fofão" no meio -> Atender (80) preempta a Vigília (60)
    await bus.publish("voice.wake_detected", {})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "atender"

    # atendimento acaba -> vigília RETOMA (alerta não foi apagado)
    await bus.publish("voice.response_finished", {})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "vigilia"

    # deixa a investigação concluir -> libera para repouso
    await asyncio.sleep(0.2)
    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus
