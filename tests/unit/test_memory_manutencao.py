"""Testes da orquestracao assincrona do banco (Cap 15 s.6, s.9)."""
import asyncio
from datetime import datetime

import pytest

from motion_core.memory.database import DatabaseManager
from motion_core.memory.manutencao import TarefaManutencao, iniciar_banco
from orion.kernel.event_bus import EventBus


def _criar_gerenciador(tmp_path) -> DatabaseManager:
    return DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")


async def _rodar_bus(bus: EventBus) -> asyncio.Task:
    return asyncio.create_task(bus.iniciar())


@pytest.mark.asyncio
async def test_iniciar_banco_publica_database_ready(tmp_path):
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("database.ready", lambda e: eventos.append(e.dados))

    db = _criar_gerenciador(tmp_path)
    await iniciar_banco(db, bus)
    await bus.aguardar_fila_vazia()

    assert len(eventos) == 1
    db.fechar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_iniciar_banco_publica_rebuilt_quando_recria(tmp_path):
    (tmp_path / "orion.db").write_bytes(b"corrompido de proposito")

    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    eventos_rebuilt = []
    bus.subscribe("database.rebuilt", lambda e: eventos_rebuilt.append(e.dados))

    db = _criar_gerenciador(tmp_path)
    await iniciar_banco(db, bus)
    await bus.aguardar_fila_vazia()

    assert len(eventos_rebuilt) == 1
    db.fechar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_executar_backup_agora_publica_backup_completed(tmp_path):
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("database.backup_completed", lambda e: eventos.append(e.dados))

    db = _criar_gerenciador(tmp_path)
    db.iniciar()
    tarefa = TarefaManutencao(db, bus)

    await tarefa.executar_backup_agora()
    await bus.aguardar_fila_vazia()

    assert len(eventos) == 1
    assert "arquivo" in eventos[0]
    db.fechar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_loop_de_manutencao_dispara_apenas_na_hora_configurada(tmp_path, monkeypatch):
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("database.backup_completed", lambda e: eventos.append(e.dados))

    db = _criar_gerenciador(tmp_path)
    db.iniciar()

    horas_simuladas = iter([2, 3, 3, 4])  # so a hora 3 deve disparar, uma unica vez

    class _DatetimeFalso(datetime):
        @classmethod
        def now(cls, tz=None):
            hora = next(horas_simuladas, 4)
            return datetime(2026, 7, 17, hora, 0, 0)

    monkeypatch.setattr("motion_core.memory.manutencao.datetime", _DatetimeFalso)

    tarefa = TarefaManutencao(db, bus, hora_backup=3, intervalo_verificacao_s=0.01)
    tarefa_loop = asyncio.create_task(tarefa.iniciar())
    await asyncio.sleep(0.15)
    tarefa.parar()
    tarefa_loop.cancel()
    try:
        await tarefa_loop
    except asyncio.CancelledError:
        pass
    await bus.aguardar_fila_vazia()

    assert len(eventos) == 1  # disparou uma vez so, apesar de duas leituras com hora==3

    db.fechar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_backup_falho_publica_backup_failed(tmp_path, monkeypatch):
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("database.backup_failed", lambda e: eventos.append(e.dados))

    db = _criar_gerenciador(tmp_path)
    db.iniciar()

    def _fazer_backup_com_falha():
        raise RuntimeError("disco cheio (simulado)")

    monkeypatch.setattr(db, "fazer_backup", _fazer_backup_com_falha)
    tarefa = TarefaManutencao(db, bus)

    with pytest.raises(RuntimeError):
        await tarefa.executar_backup_agora()
    await bus.aguardar_fila_vazia()

    assert len(eventos) == 1
    assert "disco cheio" in eventos[0]["motivo"]

    db.fechar()
    bus.parar()
    await tarefa_bus
