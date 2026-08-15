"""Testes do alimentador da memoria cognitiva (tools/alimentar_memoria.py).

Cobrem a regra de ouro do alimentador: grava pelo caminho oficial
(MemoryAPI.saber_guardar, regra 5) e e idempotente - rodar de novo nao
duplica o que ja existe.
"""
import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from motion_core.memory.api import MemoryAPI
from motion_core.memory.database import DatabaseManager
from orion.kernel.event_bus import EventBus

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "tools"))

from alimentar_memoria import alimentar, _relatorio  # noqa: E402


@pytest_asyncio.fixture
async def contexto(tmp_path):
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    db = DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")
    db.iniciar()
    api = MemoryAPI(db, bus)
    yield api, db
    db.fechar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_alimenta_e_nao_duplica(contexto):
    api, db = contexto
    seed = {
        "conhecimentos": [
            {"tipo": "conceito", "titulo": "BME280", "corpo": "Sensor I2C."},
        ],
        "procedimentos": [
            {"nome": "Ativar o modo aula", "passos": "Diga ativar modo aula."},
        ],
    }
    await alimentar(seed, api, db)
    await alimentar(seed, api, db)
    contagem = _relatorio(db)
    assert contagem["conhecimentos"] == 1
    assert contagem["procedimentos"] == 1


@pytest.mark.asyncio
async def test_ignora_categoria_fora_do_escopo(contexto):
    api, db = contexto
    seed = {"relacoes": [{"origem_tipo": "x", "origem_id": 1}]}
    await alimentar(seed, api, db)
    assert _relatorio(db)["conhecimentos"] == 0
