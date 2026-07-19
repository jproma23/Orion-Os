"""Testes da ponte memoria <-> comm.request (Cap 14 s.7; Cap 11 s.6)."""
import asyncio

import pytest

from conftest import FakeTransporte
from motion_core.memory.api import MemoryAPI
from motion_core.memory.bridge import PonteMemoria
from motion_core.memory.database import DatabaseManager
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus


async def _rodar_bus(bus: EventBus) -> asyncio.Task:
    return asyncio.create_task(bus.iniciar())


@pytest.fixture
def par_conectado():
    """Dois ComunicacaoService (mission_core e motion_core) ligados por um
    par de FakeTransporte, como em test_discovery.py."""

    def _construir():
        canal_a_para_b: asyncio.Queue = asyncio.Queue()
        canal_b_para_a: asyncio.Queue = asyncio.Queue()

        transporte_a = FakeTransporte()
        transporte_a._entrada = canal_b_para_a
        transporte_a.enviar = canal_a_para_b.put

        transporte_b = FakeTransporte()
        transporte_b._entrada = canal_a_para_b
        transporte_b.enviar = canal_b_para_a.put

        return transporte_a, transporte_b

    return _construir


@pytest.mark.asyncio
async def test_memory_recall_via_comm_request(tmp_path, par_conectado):
    bus_mission = EventBus()
    bus_motion = EventBus()
    tarefa_mission = await _rodar_bus(bus_mission)
    tarefa_motion = await _rodar_bus(bus_motion)

    servico_mission = ComunicacaoService("mission_core", bus_mission)
    servico_motion = ComunicacaoService("motion_core", bus_motion)
    transporte_mission, transporte_motion = par_conectado()
    servico_mission.adicionar_link("motion_core", transporte_mission)
    servico_motion.adicionar_link("mission_core", transporte_motion)

    db = DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")
    db.iniciar()
    api = MemoryAPI(db, bus_motion)
    ponte = PonteMemoria(api, servico_motion)
    ponte.registrar(bus_motion)

    id_pessoa = await api.remember("pessoas", {"nome": "Joao", "autorizacao": "membro_familia"})

    resposta = await servico_mission.request(
        "motion_core",
        {"comando": "memory.recall", "categoria": "pessoas", "filtro": {"id": id_pessoa}},
        timeout_s=2,
    )

    assert resposta.payload["ok"] is True
    assert resposta.payload["resultado"][0]["nome"] == "Joao"

    await servico_mission.encerrar()
    await servico_motion.encerrar()
    db.fechar()
    bus_mission.parar()
    bus_motion.parar()
    await tarefa_mission
    await tarefa_motion


@pytest.mark.asyncio
async def test_memory_remember_via_comm_request(tmp_path, par_conectado):
    bus_mission = EventBus()
    bus_motion = EventBus()
    tarefa_mission = await _rodar_bus(bus_mission)
    tarefa_motion = await _rodar_bus(bus_motion)

    servico_mission = ComunicacaoService("mission_core", bus_mission)
    servico_motion = ComunicacaoService("motion_core", bus_motion)
    transporte_mission, transporte_motion = par_conectado()
    servico_mission.adicionar_link("motion_core", transporte_mission)
    servico_motion.adicionar_link("mission_core", transporte_motion)

    db = DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")
    db.iniciar()
    api = MemoryAPI(db, bus_motion)
    ponte = PonteMemoria(api, servico_motion)
    ponte.registrar(bus_motion)

    resposta = await servico_mission.request(
        "motion_core",
        {
            "comando": "memory.remember",
            "categoria": "conhecimento",
            "dados": {"chave": "cor_favorita", "valor": "azul"},
        },
        timeout_s=2,
    )

    assert resposta.payload["ok"] is True
    novo_id = resposta.payload["resultado"]
    linhas = await api.recall("conhecimento", {"id": novo_id})
    assert linhas[0]["valor"] == "azul"

    await servico_mission.encerrar()
    await servico_motion.encerrar()
    db.fechar()
    bus_mission.parar()
    bus_motion.parar()
    await tarefa_mission
    await tarefa_motion


@pytest.mark.asyncio
async def test_comando_de_memoria_invalido_responde_ok_falso(tmp_path, par_conectado):
    bus_mission = EventBus()
    bus_motion = EventBus()
    tarefa_mission = await _rodar_bus(bus_mission)
    tarefa_motion = await _rodar_bus(bus_motion)

    servico_mission = ComunicacaoService("mission_core", bus_mission)
    servico_motion = ComunicacaoService("motion_core", bus_motion)
    transporte_mission, transporte_motion = par_conectado()
    servico_mission.adicionar_link("motion_core", transporte_mission)
    servico_motion.adicionar_link("mission_core", transporte_motion)

    db = DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")
    db.iniciar()
    api = MemoryAPI(db, bus_motion)
    ponte = PonteMemoria(api, servico_motion)
    ponte.registrar(bus_motion)

    resposta = await servico_mission.request(
        "motion_core",
        {"comando": "memory.recall", "categoria": "categoria_fantasma"},
        timeout_s=2,
    )

    assert resposta.payload["ok"] is False
    assert "categoria_fantasma" in resposta.payload["erro"]

    await servico_mission.encerrar()
    await servico_motion.encerrar()
    db.fechar()
    bus_mission.parar()
    bus_motion.parar()
    await tarefa_mission
    await tarefa_motion


@pytest.mark.asyncio
async def test_comando_nao_memoria_e_ignorado_pela_ponte(tmp_path, par_conectado):
    """Um COMMAND que nao comeca com 'memory.' nao deve gerar RESPONSE da
    ponte - fica para outro modulo tratar (ex.: MOVE_FORWARD no Fase 4)."""
    bus_mission = EventBus()
    bus_motion = EventBus()
    tarefa_mission = await _rodar_bus(bus_mission)
    tarefa_motion = await _rodar_bus(bus_motion)

    servico_mission = ComunicacaoService("mission_core", bus_mission)
    servico_motion = ComunicacaoService("motion_core", bus_motion)
    transporte_mission, transporte_motion = par_conectado()
    servico_mission.adicionar_link("motion_core", transporte_mission)
    servico_motion.adicionar_link("mission_core", transporte_motion)

    db = DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")
    db.iniciar()
    api = MemoryAPI(db, bus_motion)
    ponte = PonteMemoria(api, servico_motion)
    ponte.registrar(bus_motion)

    with pytest.raises(Exception):
        await servico_mission.request(
            "motion_core", {"comando": "MOVE_FORWARD"}, timeout_s=0.3
        )

    await servico_mission.encerrar()
    await servico_motion.encerrar()
    db.fechar()
    bus_mission.parar()
    bus_motion.parar()
    await tarefa_mission
    await tarefa_motion


def test_recall_embrulha_e_cliente_desembrulha_o_embedding():
    """Recall de pessoas devolve embedding_face (BLOB). A ponte embrulha em
    base64 (não cabe no JSON como bytes) e o MemoryClient desembrulha -
    round-trip para o reconhecimento facial ler bytes de volta."""
    import base64
    from motion_core.memory.bridge import _codificar_binarios
    from orion.mission.memory_client import _decodificar_binarios

    emb = bytes(range(200)) * 5  # bytes binários arbitrários (com \x00)
    resultado_api = [{"id": 1, "nome": "Ana", "embedding_face": emb}]

    transportado = _codificar_binarios(resultado_api)  # lado do Pi (bridge)
    assert transportado[0]["embedding_face"]["_bytes_b64"] == base64.b64encode(emb).decode()

    recebido = _decodificar_binarios(transportado)  # lado do Notebook (client)
    assert recebido[0]["embedding_face"] == emb
    assert recebido[0]["nome"] == "Ana"
