"""Testes de integracao da Fase 3 - Banco de dados e memoria (Caps 15, 11).

Cobrem o criterio de "pronto" do PLANO_IMPLEMENTACAO: migracao, backup,
replica e recuperacao funcionando, e `memory.context()` chamado do
Notebook (via comm.request) respondendo em menos de 100 ms com massa de
teste.
"""
import asyncio
import time

import pytest

from conftest import FakeTransporte
from motion_core.memory.api import MemoryAPI
from motion_core.memory.bridge import PonteMemoria
from motion_core.memory.database import DatabaseManager
from motion_core.memory.replica import ReceptorReplica, replicar_backup
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus


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


async def _popular_massa_de_teste(api: MemoryAPI, n_conversas: int = 500) -> int:
    id_pessoa = await api.remember("pessoas", {"nome": "Joao", "autorizacao": "membro_familia"})
    for i in range(n_conversas):
        await api.remember(
            "conversas",
            {"pessoa_id": id_pessoa, "papel": "usuario" if i % 2 == 0 else "robo", "texto": f"msg {i}"},
        )
    for i in range(50):
        await api.remember("conhecimento", {"chave": f"fato{i}", "valor": f"valor{i}"})
    return id_pessoa


@pytest.mark.asyncio
async def test_memory_context_via_comm_request_abaixo_de_100ms(tmp_path):
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

    id_pessoa = await _popular_massa_de_teste(api, n_conversas=500)

    inicio = time.monotonic()
    resposta = await servico_mission.request(
        "motion_core", {"comando": "memory.context", "pessoa_id": id_pessoa}, timeout_s=2
    )
    duracao_ms = (time.monotonic() - inicio) * 1000

    assert resposta.payload["ok"] is True
    assert resposta.payload["resultado"]["pessoa"]["nome"] == "Joao"
    assert duracao_ms < 100, f"memory.context() levou {duracao_ms:.1f} ms (limite: 100 ms)"

    await servico_mission.encerrar()
    await servico_motion.encerrar()
    db.fechar()
    bus_mission.parar()
    bus_motion.parar()
    await tarefa_mission
    await tarefa_motion


@pytest.mark.asyncio
async def test_ciclo_completo_backup_corrupcao_recuperacao_replica(tmp_path):
    """Cenario ponta a ponta: grava dados -> backup -> replica para o
    'notebook' -> corrompe o banco -> reabre -> recupera do backup local -
    sem depender da replica (o SSD tem seu proprio backup primeiro)."""
    bus_raspberry = EventBus()
    bus_notebook = EventBus()
    tarefa_raspberry = await _rodar_bus(bus_raspberry)
    tarefa_notebook = await _rodar_bus(bus_notebook)

    servico_raspberry = ComunicacaoService("motion_core", bus_raspberry)
    servico_notebook = ComunicacaoService("mission_core", bus_notebook)
    transporte_raspberry, transporte_notebook = _par_conectado()
    servico_raspberry.adicionar_link("mission_core", transporte_raspberry)
    servico_notebook.adicionar_link("motion_core", transporte_notebook)

    caminho_db = tmp_path / "orion.db"
    caminho_backups = tmp_path / "backups"
    db = DatabaseManager(caminho_db, caminho_backups)
    db.iniciar()
    api = MemoryAPI(db, bus_raspberry)
    await api.remember("pessoas", {"nome": "Maria", "autorizacao": "membro_familia"})

    caminho_backup = db.fazer_backup()

    receptor = ReceptorReplica(tmp_path / "replica_notebook")
    receptor.registrar(bus_notebook)
    await replicar_backup(caminho_backup, servico_raspberry, "mission_core")
    await asyncio.sleep(0.05)

    replicado = receptor.arquivo_completo(caminho_backup.name)
    assert replicado is not None
    assert replicado.read_bytes() == caminho_backup.read_bytes()

    # simula corrupcao do banco principal
    db.fechar()
    caminho_db.write_bytes(b"corrompido de proposito para o teste")

    db2 = DatabaseManager(caminho_db, caminho_backups)
    db2.iniciar()

    assert db2.foi_reconstruido is False  # recuperou do backup, nao precisou recriar do zero
    linha = db2.conexao.execute("SELECT nome FROM pessoas").fetchone()
    assert linha["nome"] == "Maria"

    db2.fechar()
    await servico_raspberry.encerrar()
    await servico_notebook.encerrar()
    bus_raspberry.parar()
    bus_notebook.parar()
    await tarefa_raspberry
    await tarefa_notebook
