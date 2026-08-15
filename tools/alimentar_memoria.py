"""Alimenta a memoria cognitiva ('Eu ja sei?') a partir de um arquivo YAML.

    .venv/bin/python tools/alimentar_memoria.py
    .venv/bin/python tools/alimentar_memoria.py --seed config/memoria_seed.yaml
    .venv/bin/python tools/alimentar_memoria.py --estado

Le `config/memoria_seed.yaml` por padrao e grava via MemoryAPI.saber_guardar
(regra 5: ninguem abre banco direto - o caminho e o mesmo do robo em
producao). Idempotente: um registro com o mesmo nome/titulo da categoria ja
existente NAO e duplicado. Reporta a contagem por categoria antes e depois.

So alimenta as categorias que o 'Eu ja sei?' busca por texto
(conhecimentos, procedimentos, dispositivos). `relacoes` e um grafo
consultado pelos extremos - nao cabe nesta alimentacao.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ))

from orion.kernel.config import ConfigurationManager  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402

# Expoe motion_core/ quando roda como script (mesmo padrao de tools/).
sys.path.insert(0, str(RAIZ / "motion_core"))
from motion_core.memory.api import MemoryAPI  # noqa: E402
from motion_core.memory.database import DatabaseManager  # noqa: E402

ARQUIVO_SEED_PADRAO = RAIZ / "config" / "memoria_seed.yaml"

#: Nome da coluna que identifica o registro em cada categoria - a chave da
#: idempotencia e do relatorio.
COLUNA_CHAVE: dict[str, str] = {
    "conhecimentos": "titulo",
    "procedimentos": "nome",
    "dispositivos": "nome",
}


def _relatorio(db: DatabaseManager) -> dict[str, int]:
    return {
        categoria: db.conexao.execute(
            f"SELECT COUNT(*) FROM {tabela}"
        ).fetchone()[0]
        for categoria, tabela in (
            ("conhecimentos", "conhecimentos"),
            ("procedimentos", "procedimentos"),
            ("dispositivos", "dispositivos"),
        )
    }


async def alimentar(seed: dict, api: MemoryAPI, db: DatabaseManager) -> None:
    antes = _relatorio(db)
    for categoria, registros in seed.items():
        coluna = COLUNA_CHAVE.get(categoria)
        if coluna is None:
            print(f"  ignorada categoria fora do escopo: {categoria}")
            continue
        for dados in registros:
            existe = db.conexao.execute(
                f"SELECT 1 FROM {categoria} WHERE {coluna} = ? LIMIT 1",
                (str(dados.get(coluna, "")),),
            ).fetchone()
            if existe:
                print(f"  ja existe {categoria}: {dados.get(coluna)}")
                continue
            await api.saber_guardar(categoria, dados)
            print(f"  gravada   {categoria}: {dados.get(coluna)}")
    depois = _relatorio(db)
    print("\ncontagem por categoria (antes -> depois):")
    for categoria in COLUNA_CHAVE:
        print(f"  {categoria:14} {antes.get(categoria, 0)} -> {depois.get(categoria, 0)}")


async def principal_async(argumentos) -> int:
    config = ConfigurationManager(str(RAIZ / "config" / "orion.yaml")).carregar()
    db_s = config.secao("database")
    caminho_db = Path(db_s.get("path", "/mnt/ssd/orion/orion.db"))
    backup_dir = Path(db_s.get("backup_dir", "/mnt/ssd/orion/backups"))

    if argumentos.estado:
        db = DatabaseManager(caminho_db, backup_dir)
        db.iniciar()
        for categoria, contagem in _relatorio(db).items():
            print(f"  {categoria:14} {contagem}")
        db.fechar()
        return 0

    caminho_seed = Path(argumentos.seed)
    if not caminho_seed.exists():
        print(f"seed nao existe: {caminho_seed}", file=sys.stderr)
        return 1
    seed = yaml.safe_load(caminho_seed.read_text(encoding="utf-8"))

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    db = DatabaseManager(caminho_db, backup_dir)
    db.iniciar()
    api = MemoryAPI(db, bus)
    try:
        await alimentar(seed, api, db)
    finally:
        db.fechar()
        bus.parar()
        await tarefa_bus
    return 0


def principal() -> int:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument(
        "--seed", default=str(ARQUIVO_SEED_PADRAO), help="arquivo YAML com o seed"
    )
    analisador.add_argument(
        "--estado", action="store_true", help="so mostra a contagem por categoria"
    )
    argumentos = analisador.parse_args()

    return asyncio.run(principal_async(argumentos))


if __name__ == "__main__":
    raise SystemExit(principal())
