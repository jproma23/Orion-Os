"""Testes da ponte de decisao estrategica (Cap 7, EDR-0022) - lado Notebook."""
import asyncio

import pytest

from conftest import FakeTransporte
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus
from orion.mission.decisao_estrategica import PonteDecisao


async def _rodar_bus(bus: EventBus) -> asyncio.Task:
    return asyncio.create_task(bus.iniciar())


def _par_conectado():
    canal_a_para_b: asyncio.Queue = asyncio.Queue()
    canal_b_para_a: asyncio.Queue = asyncio.Queue()

    transporte_a = FakeTransporte()
    transporte_a._entrada = canal_b_para_a
    transporte_a.enviar = canal_a_para_b.put

    transporte_b = FakeTransporte()
    transporte_b._entrada = canal_a_para_b
    transporte_b.enviar = canal_b_para_a.put

    return transporte_a, transporte_b


class AiManagerFalso:
    def __init__(self, acao: str | None = "observar_ambiente") -> None:
        self._acao = acao
        self.chamadas: list[tuple[dict, list[str]]] = []

    async def decidir(self, estado, acoes_validas):
        self.chamadas.append((estado, acoes_validas))
        return self._acao


@pytest.mark.asyncio
async def test_mission_decidir_via_comm_request():
    bus_pi = EventBus()
    bus_notebook = EventBus()
    tarefa_pi = await _rodar_bus(bus_pi)
    tarefa_notebook = await _rodar_bus(bus_notebook)

    servico_pi = ComunicacaoService("motion_core", bus_pi)
    servico_notebook = ComunicacaoService("mission_core", bus_notebook)
    transporte_pi, transporte_notebook = _par_conectado()
    servico_pi.adicionar_link("mission_core", transporte_pi)
    servico_notebook.adicionar_link("motion_core", transporte_notebook)

    ia_falsa = AiManagerFalso(acao="observar_ambiente")
    ponte = PonteDecisao(ia_falsa, servico_notebook)
    ponte.registrar(bus_notebook)

    resposta = await servico_pi.request(
        "mission_core",
        {
            "comando": "mission.decidir",
            "estado": {"comportamento_ativo": "repouso"},
            "acoes_validas": ["descansar", "observar_ambiente"],
        },
        timeout_s=2,
    )

    assert resposta.payload["ok"] is True
    assert resposta.payload["acao"] == "observar_ambiente"
    assert ia_falsa.chamadas == [
        ({"comportamento_ativo": "repouso"}, ["descansar", "observar_ambiente"])
    ]

    await servico_pi.encerrar()
    await servico_notebook.encerrar()
    bus_pi.parar()
    bus_notebook.parar()
    await tarefa_pi
    await tarefa_notebook


@pytest.mark.asyncio
async def test_mission_decidir_sem_acao_valida_retorna_none():
    bus_pi = EventBus()
    bus_notebook = EventBus()
    tarefa_pi = await _rodar_bus(bus_pi)
    tarefa_notebook = await _rodar_bus(bus_notebook)

    servico_pi = ComunicacaoService("motion_core", bus_pi)
    servico_notebook = ComunicacaoService("mission_core", bus_notebook)
    transporte_pi, transporte_notebook = _par_conectado()
    servico_pi.adicionar_link("mission_core", transporte_pi)
    servico_notebook.adicionar_link("motion_core", transporte_notebook)

    ponte = PonteDecisao(AiManagerFalso(acao=None), servico_notebook)
    ponte.registrar(bus_notebook)

    resposta = await servico_pi.request(
        "mission_core",
        {"comando": "mission.decidir", "estado": {}, "acoes_validas": ["descansar"]},
        timeout_s=2,
    )

    assert resposta.payload["ok"] is True
    assert resposta.payload["acao"] is None

    await servico_pi.encerrar()
    await servico_notebook.encerrar()
    bus_pi.parar()
    bus_notebook.parar()
    await tarefa_pi
    await tarefa_notebook


@pytest.mark.asyncio
async def test_comando_diferente_e_ignorado_pela_ponte():
    bus_pi = EventBus()
    bus_notebook = EventBus()
    tarefa_pi = await _rodar_bus(bus_pi)
    tarefa_notebook = await _rodar_bus(bus_notebook)

    servico_pi = ComunicacaoService("motion_core", bus_pi)
    servico_notebook = ComunicacaoService("mission_core", bus_notebook)
    transporte_pi, transporte_notebook = _par_conectado()
    servico_pi.adicionar_link("mission_core", transporte_pi)
    servico_notebook.adicionar_link("motion_core", transporte_notebook)

    ponte = PonteDecisao(AiManagerFalso(), servico_notebook)
    ponte.registrar(bus_notebook)

    with pytest.raises(Exception):
        await servico_pi.request(
            "mission_core", {"comando": "memory.recall", "categoria": "pessoas"}, timeout_s=0.3
        )

    await servico_pi.encerrar()
    await servico_notebook.encerrar()
    bus_pi.parar()
    bus_notebook.parar()
    await tarefa_pi
    await tarefa_notebook
