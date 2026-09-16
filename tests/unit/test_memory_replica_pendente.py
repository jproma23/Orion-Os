"""Testes da replica de backup auto-regenerativa (2026-08-16).

Cobre o comportamento que faltava: se o Notebook (Mission Core) esta
dormindo na hora do backup, a replica NAO se perde - entra na fila e e
reenviada sozinha quando o Notebook volta (evento comm.module_recovered).
"""
import asyncio

import pytest

from conftest import FakeTransporte
from motion_core.memory.replica import ReceptorReplica
from motion_core.memory.replica_pendente import (
    FilaReplicaPendente,
    ReplicadorAutoRegenerativo,
)
from orion.communication.service import ComunicacaoService
from orion.communication.transport import ErroTransporte
from orion.kernel.event_bus import EventBus, Prioridade


class TransporteMorto(FakeTransporte):
    """Link com o Notebook 'dormindo': falha ao enviar, como um cabo caido."""

    async def enviar(self, payload: bytes) -> None:
        raise ErroTransporte("link com o Notebook indisponivel")


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


def _montar_cena(tmp_path):
    bus_raspberry = EventBus()
    bus_notebook = EventBus()

    servico_raspberry = ComunicacaoService(
        "motion_core", bus_raspberry, max_retries=1, ack_timeout_ms=50
    )
    servico_notebook = ComunicacaoService(
        "mission_core", bus_notebook, max_retries=1, ack_timeout_ms=50
    )
    transporte_raspberry, transporte_notebook = _par_conectado()
    servico_raspberry.adicionar_link("mission_core", transporte_raspberry)
    servico_notebook.adicionar_link("motion_core", transporte_notebook)

    fila = FilaReplicaPendente(tmp_path / "replica_pendente.json")
    replicador = ReplicadorAutoRegenerativo(bus_raspberry, servico_raspberry, fila)
    replicador.registrar()

    return bus_raspberry, bus_notebook, servico_raspberry, servico_notebook, fila, replicador


@pytest.mark.asyncio
async def test_backup_sem_link_entra_na_fila_pendente(tmp_path):
    bus_raspberry, bus_notebook, servico_raspberry, servico_notebook, fila, replicador = (
        _montar_cena(tmp_path)
    )
    tarefa_raspberry = await _rodar_bus(bus_raspberry)

    # Notebook dormiu: o transporte falha ao enviar -> replica falha -> fila.
    servico_raspberry.adicionar_link("mission_core", TransporteMorto())

    backup = tmp_path / "orion_manual.db"
    backup.write_bytes(b"dados do backup sem notebook")

    await bus_raspberry.publish(
        "database.backup_completed",
        {"arquivo": str(backup)},
        prioridade=Prioridade.ALTA,
    )
    await bus_raspberry.aguardar_fila_vazia()
    await asyncio.sleep(0.05)

    assert fila.pendentes() == [str(backup)]

    await servico_raspberry.encerrar()
    await servico_notebook.encerrar()
    bus_raspberry.parar()
    bus_notebook.parar()
    await tarefa_raspberry


@pytest.mark.asyncio
async def test_replica_pendente_envia_quando_notebook_volta(tmp_path):
    bus_raspberry, bus_notebook, servico_raspberry, servico_notebook, fila, replicador = (
        _montar_cena(tmp_path)
    )
    tarefa_raspberry = await _rodar_bus(bus_raspberry)
    tarefa_notebook = await _rodar_bus(bus_notebook)

    receptor = ReceptorReplica(tmp_path / "replica_notebook")
    receptor.registrar(bus_notebook)

    backup = tmp_path / "orion_manual.db"
    backup.write_bytes(b"dados do backup que vai chegar")

    fila.adicionar(str(backup))
    assert fila.pendentes() == [str(backup)]

    # Notebook "acorda" -> heartbeat publica comm.module_recovered.
    await bus_raspberry.publish(
        "comm.module_recovered",
        {"modulo": "mission_core"},
        prioridade=Prioridade.ALTA,
    )
    await bus_raspberry.aguardar_fila_vazia()
    await asyncio.sleep(0.05)

    caminho_replicado = receptor.arquivo_completo(backup.name)
    assert caminho_replicado is not None
    assert caminho_replicado.read_bytes() == backup.read_bytes()
    assert fila.pendentes() == []

    await servico_raspberry.encerrar()
    await servico_notebook.encerrar()
    bus_raspberry.parar()
    bus_notebook.parar()
    await tarefa_raspberry
    await tarefa_notebook


@pytest.mark.asyncio
async def test_fila_persistente_sobrevive_a_reinicio(tmp_path):
    fila_origem = FilaReplicaPendente(tmp_path / "replica_pendente.json")
    fila_origem.adicionar(str(tmp_path / "backup_ontem.db"))
    fila_origem.adicionar(str(tmp_path / "backup_anteontem.db"))
    assert len(fila_origem.pendentes()) == 2

    # Novo processo (mesmo arquivo) enxerga a fila da mesma forma.
    fila_nova = FilaReplicaPendente(tmp_path / "replica_pendente.json")
    fila_nova.carregar()
    assert fila_nova.pendentes() == [
        str(tmp_path / "backup_ontem.db"),
        str(tmp_path / "backup_anteontem.db"),
    ]

    fila_nova.remover(str(tmp_path / "backup_ontem.db"))
    assert fila_nova.pendentes() == [str(tmp_path / "backup_anteontem.db")]