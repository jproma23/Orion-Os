"""Testes da API de memoria (Cap 11 s.6)."""
import asyncio

import pytest
import pytest_asyncio

from motion_core.memory.api import ErroCategoriaInvalida, ErroColunaInvalida, MemoryAPI
from motion_core.memory.database import DatabaseManager
from orion.kernel.event_bus import EventBus


def _criar_gerenciador(tmp_path) -> DatabaseManager:
    db = DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")
    db.iniciar()
    return db


@pytest_asyncio.fixture
async def contexto(tmp_path):
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    db = _criar_gerenciador(tmp_path)
    api = MemoryAPI(db, bus)
    yield api, bus
    db.fechar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_remember_preenche_timestamps_automaticos(contexto):
    api, bus = contexto
    id_pessoa = await api.remember("pessoas", {"nome": "Joao", "autorizacao": "membro_familia"})

    resultado = await api.recall("pessoas", {"id": id_pessoa})
    assert len(resultado) == 1
    assert resultado[0]["nome"] == "Joao"
    assert resultado[0]["criado_em"]
    assert resultado[0]["atualizado_em"]


@pytest.mark.asyncio
async def test_remember_publica_memory_updated(contexto):
    api, bus = contexto
    eventos = []
    bus.subscribe("memory.updated", lambda e: eventos.append(e.dados))

    await api.remember("conhecimento", {"chave": "cor_favorita", "valor": "azul"})
    await bus.aguardar_fila_vazia()

    assert len(eventos) == 1
    assert eventos[0]["operacao"] == "remember"
    assert eventos[0]["categoria"] == "conhecimento"


@pytest.mark.asyncio
async def test_recall_filtra_e_publica_recall_executed(contexto):
    api, bus = contexto
    eventos = []
    bus.subscribe("memory.recall_executed", lambda e: eventos.append(e.dados))

    await api.remember("objetos", {"classe": "caneca", "descricao": "azul"})
    await api.remember("objetos", {"classe": "livro", "descricao": "vermelho"})

    resultado = await api.recall("objetos", {"classe": "caneca"})
    await bus.aguardar_fila_vazia()

    assert len(resultado) == 1
    assert resultado[0]["classe"] == "caneca"
    assert len(eventos) == 1
    assert eventos[0]["quantidade"] == 1


@pytest.mark.asyncio
async def test_recall_respeita_limite(contexto):
    api, bus = contexto
    for i in range(5):
        await api.remember("conhecimento", {"chave": f"fato{i}", "valor": "x"})

    resultado = await api.recall("conhecimento", limite=2)
    assert len(resultado) == 2


@pytest.mark.asyncio
async def test_update_altera_registro_e_toca_atualizado_em(contexto):
    api, bus = contexto
    id_pessoa = await api.remember("pessoas", {"nome": "Joao", "autorizacao": "nenhuma"})
    original = (await api.recall("pessoas", {"id": id_pessoa}))[0]

    ok = await api.update("pessoas", id_pessoa, {"autorizacao": "membro_familia"})
    atualizado = (await api.recall("pessoas", {"id": id_pessoa}))[0]

    assert ok is True
    assert atualizado["autorizacao"] == "membro_familia"
    assert atualizado["atualizado_em"] >= original["atualizado_em"]


@pytest.mark.asyncio
async def test_update_id_inexistente_retorna_falso(contexto):
    api, bus = contexto
    assert await api.update("pessoas", 9999, {"nome": "ninguem"}) is False


@pytest.mark.asyncio
async def test_forget_remove_e_registra_log(contexto):
    api, bus = contexto
    id_objeto = await api.remember("objetos", {"classe": "chave", "descricao": "de casa"})

    removido = await api.forget("objetos", id_objeto)
    resultado = await api.recall("objetos", {"id": id_objeto})

    assert removido is True
    assert resultado == []

    logs = api._db.conexao.execute(
        "SELECT * FROM logs WHERE origem = 'memory_api'"
    ).fetchall()
    assert len(logs) == 1
    assert "objetos" in logs[0]["mensagem"]


@pytest.mark.asyncio
async def test_forget_id_inexistente_retorna_falso_sem_log(contexto):
    api, bus = contexto
    assert await api.forget("objetos", 9999) is False
    logs = api._db.conexao.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    assert logs == 0


@pytest.mark.asyncio
async def test_context_monta_pessoa_e_conversas(contexto):
    api, bus = contexto
    id_pessoa = await api.remember("pessoas", {"nome": "Joao", "autorizacao": "membro_familia"})
    await api.remember("conversas", {"pessoa_id": id_pessoa, "papel": "usuario", "texto": "oi"})
    await api.remember(
        "conversas", {"pessoa_id": id_pessoa, "papel": "robo", "texto": "ola Joao"}
    )

    contexto_ia = await api.context(id_pessoa)

    assert contexto_ia["pessoa"]["nome"] == "Joao"
    assert len(contexto_ia["conversas_recentes"]) == 2
    # ordem cronologica (mais antiga primeiro) para virar prompt da IA
    assert contexto_ia["conversas_recentes"][0]["texto"] == "oi"


@pytest.mark.asyncio
async def test_context_sem_pessoa_id_nao_falha(contexto):
    api, bus = contexto
    contexto_ia = await api.context(None)
    assert contexto_ia["pessoa"] is None
    assert contexto_ia["conversas_recentes"] == []


@pytest.mark.asyncio
async def test_stats_conta_registros(contexto):
    api, bus = contexto
    await api.remember("pessoas", {"nome": "Joao", "autorizacao": "nenhuma"})
    await api.remember("pessoas", {"nome": "Maria", "autorizacao": "nenhuma"})
    await api.remember("objetos", {"classe": "caneca"})

    estatisticas = await api.stats()

    assert estatisticas["pessoas"] == 2
    assert estatisticas["objetos"] == 1
    assert estatisticas["conversas"] == 0


@pytest.mark.asyncio
async def test_categoria_invalida_falha(contexto):
    api, bus = contexto
    with pytest.raises(ErroCategoriaInvalida):
        await api.remember("categoria_fantasma", {"x": 1})


@pytest.mark.asyncio
async def test_coluna_invalida_falha(contexto):
    """Protege contra SQL injection via nome de coluna vindo de fora
    (comm.request) - so aceita colunas que existem de verdade no schema."""
    api, bus = contexto
    with pytest.raises(ErroColunaInvalida):
        await api.remember("pessoas", {"nome; DROP TABLE pessoas;--": "malicioso"})


@pytest.mark.asyncio
async def test_coluna_invalida_no_filtro_de_recall_falha(contexto):
    api, bus = contexto
    with pytest.raises(ErroColunaInvalida):
        await api.recall("pessoas", {"coluna_que_nao_existe": 1})
