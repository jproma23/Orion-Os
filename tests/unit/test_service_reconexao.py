"""Regressão: reconectar não pode vazar socket nem tarefa.

Bug real de 2026-07-19: `adicionar_link` sobrescrevia a entrada do
dicionário e pronto - o transporte antigo continuava ABERTO e sua tarefa de
recepção continuava rodando. Como o link é declarado morto por heartbeat
atrasado (e não por socket fechado), o socket velho estava vivo: cada
"reconexão" deixava mais uma conexão ESTABLISHED para trás. Chegaram a 37.
"""
from __future__ import annotations

import asyncio

import pytest

from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus


class _TransporteFalso:
    """Transporte que registra se foi fechado e nunca entrega mensagem."""

    def __init__(self) -> None:
        self.conectado = True
        self.fechado = False

    async def enviar(self, dados: bytes) -> None:
        pass

    async def receber(self):
        # Gerador assíncrono, como os transportes de verdade. Fica em
        # silêncio para sempre - simula o link ocioso, que é exatamente o
        # estado em que o socket vazava.
        await asyncio.sleep(3600)
        yield b""

    async def fechar(self) -> None:
        self.fechado = True
        self.conectado = False


@pytest.mark.asyncio
async def test_reconectar_fecha_o_transporte_anterior() -> None:
    svc = ComunicacaoService("mission_core", EventBus())
    velho = _TransporteFalso()
    novo = _TransporteFalso()

    svc.adicionar_link("motion_core", velho)
    svc.adicionar_link("motion_core", novo)

    await asyncio.sleep(0)  # deixa a tarefa de fechamento rodar
    await asyncio.sleep(0)

    assert velho.fechado is True, "socket antigo ficou aberto - vazamento"
    assert novo.fechado is False

    await svc.encerrar()


@pytest.mark.asyncio
async def test_reconectar_cancela_a_tarefa_de_recepcao_anterior() -> None:
    """A tarefa velha lia de um socket vivo - tinha que morrer junto."""
    svc = ComunicacaoService("mission_core", EventBus())

    svc.adicionar_link("motion_core", _TransporteFalso())
    tarefa_velha = svc._tarefas_recepcao["motion_core"]

    svc.adicionar_link("motion_core", _TransporteFalso())
    await asyncio.sleep(0)

    assert tarefa_velha.cancelled() or tarefa_velha.cancelling() > 0
    assert svc._tarefas_recepcao["motion_core"] is not tarefa_velha

    await svc.encerrar()


@pytest.mark.asyncio
async def test_muitas_reconexoes_nao_acumulam_tarefas() -> None:
    """O caso observado em campo: reconexões seguidas em laço."""
    svc = ComunicacaoService("mission_core", EventBus())
    transportes = [_TransporteFalso() for _ in range(20)]

    for t in transportes:
        svc.adicionar_link("motion_core", t)
        await asyncio.sleep(0)

    # Uma tarefa por peer, não vinte.
    assert len(svc._tarefas_recepcao) == 1
    assert len(svc._links) == 1

    await asyncio.sleep(0)
    # Todos os anteriores fechados; só o último segue aberto.
    assert all(t.fechado for t in transportes[:-1]), "sobrou socket aberto"
    assert transportes[-1].fechado is False

    await svc.encerrar()


@pytest.mark.asyncio
async def test_links_diferentes_convivem() -> None:
    """Fechar o link antigo de um peer não pode derrubar os outros peers."""
    svc = ComunicacaoService("motion_core", EventBus())
    arduino = _TransporteFalso()
    notebook_velho = _TransporteFalso()
    notebook_novo = _TransporteFalso()

    svc.adicionar_link("hardware_core", arduino)
    svc.adicionar_link("mission_core", notebook_velho)
    svc.adicionar_link("mission_core", notebook_novo)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert arduino.fechado is False, "link do Arduino não podia ser afetado"
    assert notebook_velho.fechado is True
    assert len(svc._links) == 2

    await svc.encerrar()
