"""Testes do MemoryClient (Cap 7 s.2, 5) - cliente de memoria do Notebook
sobre comm.request, respondido por uma PonteMemoria (Fase 3) do outro lado.
"""
import asyncio

import pytest
import pytest_asyncio

from conftest import FakeTransporte
from motion_core.memory.api import MemoryAPI
from motion_core.memory.bridge import PonteMemoria
from motion_core.memory.database import DatabaseManager
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus
from orion.mission.memory_client import ErroMemoriaRemota, MemoryClient


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


@pytest_asyncio.fixture
async def montagem(tmp_path):
    bus_mission = EventBus()
    bus_motion = EventBus()
    tarefa_mission = await _rodar_bus(bus_mission)
    tarefa_motion = await _rodar_bus(bus_motion)

    servico_mission = ComunicacaoService("mission_core", bus_mission)
    servico_motion = ComunicacaoService("motion_core", bus_motion)
    transporte_mission, transporte_motion = _par_conectado()
    servico_mission.adicionar_link("motion_core", transporte_mission)
    servico_motion.adicionar_link("mission_core", transporte_motion)

    db = DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")
    db.iniciar()
    api = MemoryAPI(db, bus_motion)
    ponte = PonteMemoria(api, servico_motion)
    ponte.registrar(bus_motion)

    cliente = MemoryClient(servico_mission)

    yield cliente, api

    await servico_mission.encerrar()
    await servico_motion.encerrar()
    db.fechar()
    bus_mission.parar()
    bus_motion.parar()
    await tarefa_mission
    await tarefa_motion


@pytest.mark.asyncio
async def test_remember_via_cliente(montagem):
    cliente, api = montagem
    novo_id = await cliente.remember("conhecimento", {"chave": "cor", "valor": "azul"})
    assert isinstance(novo_id, int)

    linhas = await api.recall("conhecimento", {"id": novo_id})
    assert linhas[0]["valor"] == "azul"


@pytest.mark.asyncio
async def test_recall_via_cliente(montagem):
    cliente, api = montagem
    await api.remember("pessoas", {"nome": "Joao", "autorizacao": "membro_familia"})

    resultado = await cliente.recall("pessoas", {"nome": "Joao"})

    assert len(resultado) == 1
    assert resultado[0]["nome"] == "Joao"


@pytest.mark.asyncio
async def test_context_via_cliente(montagem):
    cliente, api = montagem
    id_pessoa = await api.remember("pessoas", {"nome": "Maria", "autorizacao": "nenhuma"})

    contexto = await cliente.context(id_pessoa)

    assert contexto["pessoa"]["nome"] == "Maria"


@pytest.mark.asyncio
async def test_erro_remoto_levanta_excecao(montagem):
    cliente, _api = montagem
    with pytest.raises(ErroMemoriaRemota):
        await cliente.recall("categoria_fantasma")
