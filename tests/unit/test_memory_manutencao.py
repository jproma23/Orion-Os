"""Testes da orquestracao assincrona do banco (Cap 15 s.6, s.9)."""
import asyncio
import time
from datetime import datetime

import pytest

from motion_core.memory.api import MemoryAPI
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


@pytest.mark.asyncio
async def test_loop_sobrevive_a_falha_de_backup_e_tenta_de_novo(tmp_path, monkeypatch):
    """Achado real da vistoria de 2026-07-24: antes deste fix, uma falha de
    backup (ex.: SSD cheio por uma noite) matava a task de manutencao pra
    sempre - nenhum backup nem limpeza de retencao rodava de novo ate
    reiniciar o processo inteiro. Agora o loop sobrevive e tenta de novo no
    proximo ciclo de verificacao, ainda dentro da mesma hora configurada."""
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    falhas = []
    completos = []
    bus.subscribe("database.backup_failed", lambda e: falhas.append(e.dados))
    bus.subscribe("database.backup_completed", lambda e: completos.append(e.dados))

    db = _criar_gerenciador(tmp_path)
    db.iniciar()

    fazer_backup_original = db.fazer_backup
    chamadas = {"n": 0}

    def _fazer_backup_falha_na_primeira():
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise RuntimeError("disco cheio (simulado)")
        return fazer_backup_original()

    monkeypatch.setattr(db, "fazer_backup", _fazer_backup_falha_na_primeira)

    class _DatetimeFixoAs3h(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 17, 3, 0, 0)

    monkeypatch.setattr("motion_core.memory.manutencao.datetime", _DatetimeFixoAs3h)

    tarefa = TarefaManutencao(db, bus, hora_backup=3, intervalo_verificacao_s=0.01)
    tarefa_loop = asyncio.create_task(tarefa.iniciar())
    await asyncio.sleep(0.1)
    tarefa.parar()
    tarefa_loop.cancel()
    try:
        await tarefa_loop
    except asyncio.CancelledError:
        pass
    await bus.aguardar_fila_vazia()

    assert len(falhas) >= 1  # primeira tentativa falhou e foi publicada
    assert len(completos) >= 1  # loop nao morreu - tentou de novo e completou
    assert chamadas["n"] >= 2  # de fato tentou mais de uma vez

    db.fechar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_backup_segura_o_lock_compartilhado_com_memory_api(tmp_path, monkeypatch):
    """Achado real da vistoria de 2026-07-24: WebUIServer chama
    MemoryAPI direto de um handler HTTP, e TarefaManutencao roda como
    task independente - os dois usando asyncio.to_thread na mesma
    conexao sqlite3, sem lock nenhum antes deste fix. Aqui provamos que
    executar_backup_agora() de fato segura db.lock enquanto roda (nao so
    "deveria")."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())

    db = _criar_gerenciador(tmp_path)
    db.iniciar()
    tarefa = TarefaManutencao(db, bus)

    lock_estava_ocupado = asyncio.Event()
    fazer_backup_original = db.fazer_backup

    def _fazer_backup_devagar():
        time.sleep(0.1)  # janela generosa pra checagem abaixo conseguir ver
        return fazer_backup_original()

    monkeypatch.setattr(db, "fazer_backup", _fazer_backup_devagar)

    async def _checar_lock_no_meio_do_backup():
        await asyncio.sleep(0.03)  # da tempo do backup ja ter pego o lock
        if db.lock.locked():
            lock_estava_ocupado.set()

    await asyncio.gather(tarefa.executar_backup_agora(), _checar_lock_no_meio_do_backup())

    assert lock_estava_ocupado.is_set()

    db.fechar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_memory_api_tambem_segura_o_mesmo_lock(tmp_path, monkeypatch):
    """Mesmo achado do teste acima, do lado do MemoryAPI (o caminho que o
    WebUIServer usa de verdade em motion_core/webui/server.py)."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())

    db = _criar_gerenciador(tmp_path)
    db.iniciar()
    memory_api = MemoryAPI(db, bus)

    lock_estava_ocupado = asyncio.Event()
    validar_original = memory_api._validar_colunas

    def _validar_devagar(tabela, colunas):
        time.sleep(0.1)
        return validar_original(tabela, colunas)

    monkeypatch.setattr(memory_api, "_validar_colunas", _validar_devagar)

    async def _checar_lock_no_meio_do_remember():
        await asyncio.sleep(0.03)
        if db.lock.locked():
            lock_estava_ocupado.set()

    await asyncio.gather(
        memory_api.remember("pessoas", {"nome": "teste"}),
        _checar_lock_no_meio_do_remember(),
    )

    assert lock_estava_ocupado.is_set()

    db.fechar()
    bus.parar()
    await tarefa_bus
