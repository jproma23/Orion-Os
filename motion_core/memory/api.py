"""API de memoria (Cap 11 secao 6) - unico ponto de acesso ao banco.

Operacoes minimas: remember, recall, update, forget, context, stats. Toda
operacao publica um evento no Event Bus (memory.updated para mutacoes,
memory.recall_executed para consultas) para diagnostico (Cap 11 s.6).

Metodos assincronos: o trabalho de banco (sqlite3, bloqueante) roda em
thread separada via `asyncio.to_thread`, para nao travar o loop de eventos
nem o laco de navegacao (Cap 11 s.9; Cap 15 s.8).
"""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from motion_core.memory.database import DatabaseManager, agora_iso
from motion_core.memory.schema import TABELAS_POR_CATEGORIA
from orion.kernel.event_bus import EventBus

#: Colunas de timestamp preenchidas automaticamente em remember(), quando o
#: chamador nao as fornece - poupa cada modulo de repetir esse detalhe.
_CAMPOS_CRIACAO_AUTOMATICOS: dict[str, tuple[str, ...]] = {
    "pessoas": ("criado_em", "atualizado_em"),
    "ambientes": ("criado_em",),
    "objetos": ("visto_em",),
    "conhecimento": ("criado_em",),
    "conversas": ("timestamp",),
    "missoes": ("inicio",),
    "eventos": ("timestamp",),
}

#: Coluna "tocada" automaticamente em update(), quando a tabela tem uma.
_CAMPO_ATUALIZACAO_AUTOMATICO: dict[str, str] = {
    "pessoas": "atualizado_em",
}


class ErroCategoriaInvalida(Exception):
    """Categoria de memoria desconhecida (fora de TABELAS_POR_CATEGORIA)."""


class ErroColunaInvalida(Exception):
    """Nome de coluna fora do schema real da tabela.

    `dados`/`filtro` podem vir de uma requisicao remota (comm.request,
    Cap 14) - os nomes de coluna nunca sao interpolados na SQL sem antes
    serem validados contra o schema de verdade (PRAGMA table_info), para
    nao abrir brecha de SQL injection via nome de campo.
    """


class MemoryAPI:
    def __init__(self, db: DatabaseManager, event_bus: EventBus) -> None:
        self._db = db
        self._event_bus = event_bus
        self._colunas_por_tabela: dict[str, set[str]] = {}

    def _tabela(self, categoria: str) -> str:
        tabela = TABELAS_POR_CATEGORIA.get(categoria)
        if tabela is None:
            raise ErroCategoriaInvalida(f"Categoria de memoria desconhecida: '{categoria}'")
        return tabela

    def _colunas_validas(self, tabela: str) -> set[str]:
        if tabela not in self._colunas_por_tabela:
            linhas = self._db.conexao.execute(f"PRAGMA table_info({tabela})").fetchall()
            self._colunas_por_tabela[tabela] = {linha["name"] for linha in linhas}
        return self._colunas_por_tabela[tabela]

    def _validar_colunas(self, tabela: str, colunas: Any) -> None:
        invalidas = set(colunas) - self._colunas_validas(tabela)
        if invalidas:
            raise ErroColunaInvalida(f"Coluna(s) inexistente(s) em '{tabela}': {sorted(invalidas)}")

    async def remember(self, categoria: str, dados: dict[str, Any]) -> int:
        """Insere um novo registro na categoria indicada. Retorna o id gerado."""
        tabela = self._tabela(categoria)
        completos = dict(dados)
        for coluna in _CAMPOS_CRIACAO_AUTOMATICOS.get(categoria, ()):
            completos.setdefault(coluna, agora_iso())

        def _inserir() -> int:
            self._validar_colunas(tabela, completos.keys())
            colunas = ", ".join(completos.keys())
            marcadores = ", ".join("?" for _ in completos)
            cursor = self._db.conexao.execute(
                f"INSERT INTO {tabela} ({colunas}) VALUES ({marcadores})",
                tuple(completos.values()),
            )
            return cursor.lastrowid

        novo_id = await asyncio.to_thread(_inserir)
        await self._event_bus.publish(
            "memory.updated", {"categoria": categoria, "operacao": "remember", "id": novo_id}
        )
        return novo_id

    async def recall(
        self, categoria: str, filtro: dict[str, Any] | None = None, limite: int = 20
    ) -> list[dict[str, Any]]:
        """Busca registros da categoria, filtrando por igualdade nas colunas
        de `filtro`, mais recentes primeiro, limitados a `limite`."""
        tabela = self._tabela(categoria)
        filtro = filtro or {}

        def _buscar() -> list[dict[str, Any]]:
            self._validar_colunas(tabela, filtro.keys())
            condicao = " AND ".join(f"{coluna} = ?" for coluna in filtro) or "1=1"
            sql = f"SELECT * FROM {tabela} WHERE {condicao} ORDER BY id DESC LIMIT ?"
            linhas = self._db.conexao.execute(sql, (*filtro.values(), limite)).fetchall()
            return [dict(linha) for linha in linhas]

        resultado = await asyncio.to_thread(_buscar)
        await self._event_bus.publish(
            "memory.recall_executed",
            {"categoria": categoria, "filtro": filtro, "quantidade": len(resultado)},
        )
        return resultado

    async def update(self, categoria: str, id: int, dados: dict[str, Any]) -> bool:
        """Atualiza um registro existente. Retorna False se o id nao existir."""
        tabela = self._tabela(categoria)
        completos = dict(dados)
        campo_auto = _CAMPO_ATUALIZACAO_AUTOMATICO.get(categoria)
        if campo_auto is not None:
            completos.setdefault(campo_auto, agora_iso())

        def _atualizar() -> bool:
            self._validar_colunas(tabela, completos.keys())
            atribuicoes = ", ".join(f"{coluna} = ?" for coluna in completos)
            cursor = self._db.conexao.execute(
                f"UPDATE {tabela} SET {atribuicoes} WHERE id = ?",
                (*completos.values(), id),
            )
            return cursor.rowcount > 0

        atualizado = await asyncio.to_thread(_atualizar)
        await self._event_bus.publish(
            "memory.updated",
            {"categoria": categoria, "operacao": "update", "id": id, "aplicado": atualizado},
        )
        return atualizado

    async def forget(self, categoria: str, id: int) -> bool:
        """Remove um registro. Toda exclusao e registrada em `logs`
        (Cap 11 s.8). Retorna False se o id nao existir."""
        tabela = self._tabela(categoria)

        def _remover() -> bool:
            cursor = self._db.conexao.execute(f"DELETE FROM {tabela} WHERE id = ?", (id,))
            removido = cursor.rowcount > 0
            if removido:
                self._db.conexao.execute(
                    "INSERT INTO logs (nivel, origem, mensagem, contexto_json, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        "INFO",
                        "memory_api",
                        f"Registro removido: {categoria}#{id}",
                        f'{{"categoria": "{categoria}", "id": {id}}}',
                        agora_iso(),
                    ),
                )
            return removido

        removido = await asyncio.to_thread(_remover)
        await self._event_bus.publish(
            "memory.updated",
            {"categoria": categoria, "operacao": "forget", "id": id, "aplicado": removido},
        )
        return removido

    async def context(self, pessoa_id: int | None = None, limite_conversas: int = 10) -> dict:
        """Monta o contexto persistido para a IA (Cap 11 s.3/s.6): dados da
        pessoa (se identificada), janela de conversa recente e conhecimento
        relevante. Estado em RAM (missao em execucao, sensores, etc.) fica a
        cargo do Mission Core - nao vem do banco."""

        def _montar() -> dict:
            pessoa = None
            conversas: list[dict] = []
            if pessoa_id is not None:
                linha = self._db.conexao.execute(
                    "SELECT * FROM pessoas WHERE id = ?", (pessoa_id,)
                ).fetchone()
                pessoa = dict(linha) if linha is not None else None
                conversas = [
                    dict(linha)
                    for linha in self._db.conexao.execute(
                        "SELECT * FROM conversas WHERE pessoa_id = ? "
                        "ORDER BY timestamp DESC LIMIT ?",
                        (pessoa_id, limite_conversas),
                    ).fetchall()
                ]
            conhecimento = [
                dict(linha)
                for linha in self._db.conexao.execute(
                    "SELECT * FROM conhecimento ORDER BY criado_em DESC LIMIT 20"
                ).fetchall()
            ]
            return {
                "pessoa": pessoa,
                "conversas_recentes": list(reversed(conversas)),
                "conhecimento_relevante": conhecimento,
            }

        contexto = await asyncio.to_thread(_montar)
        await self._event_bus.publish(
            "memory.recall_executed", {"categoria": "context", "pessoa_id": pessoa_id}
        )
        return contexto

    async def stats(self) -> dict[str, int]:
        """Estatisticas de uso: quantidade de registros por categoria (Cap 11 s.6)."""

        def _contar() -> dict[str, int]:
            contagens = {}
            for categoria, tabela in TABELAS_POR_CATEGORIA.items():
                try:
                    contagens[categoria] = self._db.conexao.execute(
                        f"SELECT COUNT(*) FROM {tabela}"
                    ).fetchone()[0]
                except sqlite3.OperationalError:
                    contagens[categoria] = 0
            return contagens

        estatisticas = await asyncio.to_thread(_contar)
        await self._event_bus.publish("memory.recall_executed", {"categoria": "stats"})
        return estatisticas
