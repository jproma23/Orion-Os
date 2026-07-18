"""Testes da replica cruzada de backup (Cap 15 s.6)."""
import asyncio
import os

import pytest

from conftest import FakeTransporte
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


@pytest.mark.asyncio
async def test_replica_reconstroi_arquivo_identico(tmp_path):
    bus_raspberry = EventBus()
    bus_notebook = EventBus()
    tarefa_raspberry = await _rodar_bus(bus_raspberry)
    tarefa_notebook = await _rodar_bus(bus_notebook)

    servico_raspberry = ComunicacaoService("motion_core", bus_raspberry)
    servico_notebook = ComunicacaoService("mission_core", bus_notebook)
    transporte_raspberry, transporte_notebook = _par_conectado()
    servico_raspberry.adicionar_link("mission_core", transporte_raspberry)
    servico_notebook.adicionar_link("motion_core", transporte_notebook)

    db = DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")
    db.iniciar()
    db.conexao.execute(
        "INSERT INTO pessoas (nome, criado_em, atualizado_em) VALUES (?, ?, ?)",
        ("Joao", "2026-07-17T00:00:00Z", "2026-07-17T00:00:00Z"),
    )
    caminho_backup = db.fazer_backup()

    receptor = ReceptorReplica(tmp_path / "replica_notebook")
    receptor.registrar(bus_notebook)

    total_enviado = await replicar_backup(
        caminho_backup, servico_raspberry, destino="mission_core", tamanho_chunk=64
    )
    await asyncio.sleep(0.05)

    caminho_replicado = receptor.arquivo_completo(caminho_backup.name)
    assert caminho_replicado is not None
    assert total_enviado > 1  # confirma que o teste realmente exercitou varios blocos
    assert caminho_replicado.read_bytes() == caminho_backup.read_bytes()

    await servico_raspberry.encerrar()
    await servico_notebook.encerrar()
    db.fechar()
    bus_raspberry.parar()
    bus_notebook.parar()
    await tarefa_raspberry
    await tarefa_notebook


@pytest.mark.asyncio
async def test_replica_de_arquivo_pequeno_um_bloco_so(tmp_path):
    bus_raspberry = EventBus()
    bus_notebook = EventBus()
    tarefa_raspberry = await _rodar_bus(bus_raspberry)
    tarefa_notebook = await _rodar_bus(bus_notebook)

    servico_raspberry = ComunicacaoService("motion_core", bus_raspberry)
    servico_notebook = ComunicacaoService("mission_core", bus_notebook)
    transporte_raspberry, transporte_notebook = _par_conectado()
    servico_raspberry.adicionar_link("mission_core", transporte_raspberry)
    servico_notebook.adicionar_link("motion_core", transporte_notebook)

    arquivo_pequeno = tmp_path / "orion_pequeno.db"
    arquivo_pequeno.write_bytes(b"conteudo pequeno de teste")

    receptor = ReceptorReplica(tmp_path / "replica_notebook")
    receptor.registrar(bus_notebook)

    total_enviado = await replicar_backup(arquivo_pequeno, servico_raspberry, "mission_core")
    await asyncio.sleep(0.05)

    assert total_enviado == 1
    caminho_replicado = receptor.arquivo_completo(arquivo_pequeno.name)
    assert caminho_replicado.read_bytes() == arquivo_pequeno.read_bytes()

    await servico_raspberry.encerrar()
    await servico_notebook.encerrar()
    bus_raspberry.parar()
    bus_notebook.parar()
    await tarefa_raspberry
    await tarefa_notebook


@pytest.mark.asyncio
async def test_receptor_ignora_comandos_que_nao_sao_replica(tmp_path):
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    receptor = ReceptorReplica(tmp_path / "replica_notebook")
    receptor.registrar(bus)

    from orion.kernel.event_bus import Prioridade

    await bus.publish(
        "comm.mensagem.command", {"payload": {"comando": "WHO_ARE_YOU"}}, prioridade=Prioridade.NORMAL
    )
    await bus.aguardar_fila_vazia()

    assert receptor._blocos_em_andamento == {}
    assert os.listdir(tmp_path / "replica_notebook") == []

    bus.parar()
    await tarefa_bus
