"""Testes da memoria cognitiva (saber) - o "Eu ja sei?" do ORION.

Cobrem o caminho inteiro que o layout desta sessao desenha: o robo guarda
o que aprendeu (`saber_guardar`), responde do proprio acervo quando a fala
cobriu o registro (`saber_buscar` com cobertura) e conta quanto consultou a
IA (`registrar_consulta_ia` + `saber_stats`) - a medida de dependencia.
"""
import asyncio

import pytest
import pytest_asyncio

from motion_core.memory.api import MemoryAPI
from motion_core.memory.database import DatabaseManager
from orion.kernel.event_bus import EventBus


@pytest_asyncio.fixture
async def contexto(tmp_path):
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    db = DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")
    db.iniciar()
    api = MemoryAPI(db, bus)
    yield api, bus
    db.fechar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_migracao_cria_tabelas_cognitivas(contexto):
    api, _ = contexto
    tabelas = [
        r["name"]
        for r in api._db.conexao.execute(
            "SELECT name FROM sqlite_master WHERE type='table' OR type='virtual'"
        ).fetchall()
    ]
    for esperada in (
        "conhecimentos", "experiencias", "procedimentos",
        "dispositivos", "relacoes", "saber_fts",
    ):
        assert esperada in tabelas, f"tabela {esperada} ausente"


@pytest.mark.asyncio
async def test_saber_guardar_e_buscar_pelo_conceito(contexto):
    api, _ = contexto
    await api.saber_guardar(
        "conhecimentos",
        {
            "tipo": "conceito",
            "titulo": "BME280",
            "corpo": "Sensor de temperatura, pressao e umidade, "
            "comunicacao I2C, endereco padrao 0x76.",
            "fonte": "dono",
            "confianca": 0.9,
        },
    )

    achados = await api.saber_buscar("o que é um BME280?", limite=3)
    assert len(achados) == 1
    assert achados[0]["categoria"] == "conhecimentos"
    assert achados[0]["titulo"] == "BME280"
    assert "I2C" in achados[0]["corpo"]
    assert "nota_bm25" in achados[0]


@pytest.mark.asyncio
async def test_saber_busca_procedimento_normaliza_titulo_corpo(contexto):
    api, _ = contexto
    await api.saber_guardar(
        "procedimentos",
        {"nome": "Ler sensor de distancia", "passos": "Pingar, esperar o eco."},
    )

    achados = await api.saber_buscar("como leio o sensor de distancia", limite=3)
    assert len(achados) == 1
    assert achados[0]["categoria"] == "procedimentos"
    assert achados[0]["titulo"] == "Ler sensor de distancia"


@pytest.mark.asyncio
async def test_saber_nao_responde_palavra_solta_sem_cobertura(contexto):
    api, _ = contexto
    await api.saber_guardar(
        "conhecimentos",
        {"titulo": "Lampada", "corpo": "Di jpa LED na cabeca, liga e apaga."},
    )

    # Uma palavra generica ("isso") nao cobre o assunto - nada responde.
    assert await api.saber_buscar("isso ai doutor", limite=3) == []
    # Ate a pergunta legitima responde.
    assert len(await api.saber_buscar("o que e a lampada", limite=3)) == 1


@pytest.mark.asyncio
async def test_saber_contador_de_consultas_ia(contexto):
    api, _ = contexto

    await api.registrar_consulta_ia()
    await api.registrar_consulta_ia()

    stats = await api.saber_stats()
    assert stats["consultas_ia"] == 2
    assert stats["total_conhecimento_local"] == 0


@pytest.mark.asyncio
async def test_saber_stats_conta_quatro_categorias(contexto):
    api, _ = contexto
    await api.saber_guardar("conhecimentos", {"titulo": "A", "corpo": "x"})
    await api.saber_guardar("experiencias", {"situacao": "caiu", "o_que_aconteceu": "tombou"})
    await api.saber_guardar("procedimentos", {"nome": "Subir", "passos": "girar"})
    await api.saber_guardar("dispositivos", {"nome": "led", "tipo": "led", "acesso": "GPIO17"})
    await api.saber_guardar("relacoes", {
        "origem_tipo": "dispositivo", "origem_id": 1, "relacao": "alimenta",
        "destino_tipo": "skill", "destino_id": 1,
    })

    stats = await api.saber_stats()
    assert stats["conhecimentos"] == 1
    assert stats["experiencias"] == 1
    assert stats["procedimentos"] == 1
    assert stats["dispositivos"] == 1
    assert stats["relacoes"] == 1
    assert stats["total_conhecimento_local"] == 5