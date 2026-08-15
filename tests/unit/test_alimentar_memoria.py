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

from alimentar_memoria import alimentar, _relatorio, _carregar_saber  # noqa: E402


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


def test_carrega_pasta_inteira(tmp_path):
    area1 = tmp_path / "area_dono.yaml"
    area1.write_text(
        "conhecimentos:\n"
        "  - tipo: conceito\n"
        "    titulo: Perfil do dono\n"
        "    corpo: Tecnico em Eletromecanica.\n",
        encoding="utf-8",
    )
    sub = tmp_path / "codigo"
    sub.mkdir()
    area2 = sub / "codigo_src.yaml"
    area2.write_text(
        "conhecimentos:\n"
        "  - tipo: conceito\n"
        "    titulo: src/orion/main.py\n"
        "    corpo: Ponto de entrada.\n",
        encoding="utf-8",
    )
    (tmp_path / "ignorado.txt").write_text("nao e yaml", encoding="utf-8")

    seed = _carregar_saber(tmp_path)

    assert seed["conhecimentos"][0]["titulo"] == "Perfil do dono"
    assert seed["conhecimentos"][1]["titulo"] == "src/orion/main.py"
    assert len(seed["conhecimentos"]) == 2


def test_carrega_arquivo_unico(tmp_path):
    arquivo = tmp_path / "seed.yaml"
    arquivo.write_text(
        "procedimentos:\n"
        "  - nome: Ativar o modo aula\n"
        "    passos: Diga ativar modo aula.\n",
        encoding="utf-8",
    )

    seed = _carregar_saber(arquivo)

    assert seed["procedimentos"][0]["nome"] == "Ativar o modo aula"
