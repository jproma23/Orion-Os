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

        async with self._db.lock:
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

        async with self._db.lock:
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

        async with self._db.lock:
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

        async with self._db.lock:
            removido = await asyncio.to_thread(_remover)
        await self._event_bus.publish(
            "memory.updated",
            {"categoria": categoria, "operacao": "forget", "id": id, "aplicado": removido},
        )
        return removido

    #: Palavras curtas demais para discriminar qualquer coisa. Sem este filtro,
    #: "e", "de" e "o" casariam com quase todo fato guardado e a busca
    #: devolveria tudo - o mesmo resultado de nao buscar.
    _MINIMO_LETRAS_BUSCA = 4

    def _conhecimento_relevante(self, sobre: str | None, limite: int = 20) -> list[dict]:
        """Fatos que casam com a fala atual, completados pelos mais recentes."""
        import re

        encontrados: list[dict] = []
        vistos: set[int] = set()

        if sobre:
            palavras = [
                p
                for p in re.findall(r"[\wÀ-ÿ]+", sobre.lower())
                if len(p) >= self._MINIMO_LETRAS_BUSCA
            ][:6]  # teto: 6 palavras ja cobrem uma frase falada
            for palavra in palavras:
                alvo = f"%{palavra}%"
                for linha in self._db.conexao.execute(
                    "SELECT * FROM conhecimento "
                    "WHERE lower(chave) LIKE ? OR lower(valor) LIKE ? "
                    "ORDER BY confianca DESC, criado_em DESC LIMIT ?",
                    (alvo, alvo, limite),
                ).fetchall():
                    registro = dict(linha)
                    if registro["id"] in vistos:
                        continue
                    vistos.add(registro["id"])
                    encontrados.append(registro)

        if len(encontrados) < limite:
            for linha in self._db.conexao.execute(
                "SELECT * FROM conhecimento ORDER BY criado_em DESC LIMIT ?",
                (limite,),
            ).fetchall():
                registro = dict(linha)
                if registro["id"] in vistos:
                    continue
                vistos.add(registro["id"])
                encontrados.append(registro)
                if len(encontrados) >= limite:
                    break

        return encontrados[:limite]

    async def context(
        self,
        pessoa_id: int | None = None,
        limite_conversas: int = 10,
        sobre: str | None = None,
    ) -> dict:
        """Monta o contexto persistido para a IA (Cap 11 s.3/s.6): dados da
        pessoa (se identificada), janela de conversa recente e conhecimento
        relevante. Estado em RAM (missao em execucao, sensores, etc.) fica a
        cargo do Mission Core - nao vem do banco."""

        def _montar() -> dict:
            pessoa = None
            if pessoa_id is not None:
                linha = self._db.conexao.execute(
                    "SELECT * FROM pessoas WHERE id = ?", (pessoa_id,)
                ).fetchone()
                pessoa = dict(linha) if linha is not None else None

            # AS CONVERSAS VOLTAM MESMO SEM PESSOA IDENTIFICADA (2026-08-02).
            #
            # Ate aqui a busca inteira vivia dentro de `if pessoa_id is not
            # None`, entao `conversas_recentes` voltava SEMPRE VAZIO na
            # pratica: as 146 linhas gravadas na tabela tinham todas
            # `pessoa_id = None`, porque o robo conversa por voz sem
            # necessariamente reconhecer o rosto de quem fala. O robo guardava
            # tudo e nunca lia nada - o consumidor existia (ai_manager monta o
            # bloco no prompt) e o produtor devolvia lista vazia.
            #
            # Com pessoa identificada, continua filtrando por ela: e mais
            # preciso. Sem pessoa, traz as ultimas de qualquer um - imperfeito
            # numa casa com varias pessoas, e infinitamente melhor que nada.
            # AS DA PESSOA PRIMEIRO, E O RECENTE COMPLETA (2026-08-03).
            #
            # Filtrar SO pela pessoa parece mais preciso e foi uma regressao
            # real: quando o reconhecimento facial passou a preencher
            # `pessoa_id`, a consulta encontrou ZERO linhas - as 202 conversas
            # ja gravadas tem todas `pessoa_id = NULL`, porque foram gravadas
            # antes de existir quem as atribuisse. O robo perdeu o historico
            # inteiro no instante em que aprendeu de quem ele era, e passou a
            # responder mais seco. O dono percebeu na hora: *"antes ele estava
            # conversando bastante respondendo tudo, agora ele parou"*.
            #
            # E o espelho exato do bug de 02/08, quando a busca vivia dentro de
            # `if pessoa_id is not None` e devolvia sempre vazio. Da outra vez
            # o filtro nunca acertava; desta vez ele acerta e nao ha o que
            # achar. A licao e a mesma: **precisao que devolve nada e pior que
            # aproximacao que devolve algo.**
            #
            # Mesmo idioma de `_conhecimento_relevante`: o especifico primeiro,
            # o resto completa por data. Conforme as conversas novas forem
            # ganhando dono, a parte de baixo encolhe sozinha - sem migracao,
            # sem data marcada.
            conversas: list[dict] = []
            vistos: set = set()
            if pessoa_id is not None:
                for linha in self._db.conexao.execute(
                    "SELECT * FROM conversas WHERE pessoa_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (pessoa_id, limite_conversas),
                ).fetchall():
                    registro = dict(linha)
                    vistos.add(registro["id"])
                    conversas.append(registro)

            if len(conversas) < limite_conversas:
                for linha in self._db.conexao.execute(
                    "SELECT * FROM conversas ORDER BY timestamp DESC LIMIT ?",
                    (limite_conversas,),
                ).fetchall():
                    registro = dict(linha)
                    if registro["id"] in vistos:
                        continue
                    vistos.add(registro["id"])
                    conversas.append(registro)
                    if len(conversas) >= limite_conversas:
                        break

            # Reordena por tempo: a mistura das duas consultas pode intercalar,
            # e conversa fora de ordem cronologica confunde o modelo.
            conversas.sort(key=lambda c: str(c.get("timestamp") or ""), reverse=True)
            conversas = conversas[:limite_conversas]

            # CONHECIMENTO *RELEVANTE*, e nao apenas recente.
            #
            # O nome do campo prometia relevancia desde sempre; a consulta era
            # `ORDER BY criado_em DESC LIMIT 20`, ou seja os 20 ultimos fatos
            # independentemente do assunto. Com a tabela vazia isso nunca
            # apareceu; com ela cheia, o robo levaria 20 fatos aleatorios ao
            # prompt e o que importa para a pergunta ficaria de fora.
            #
            # A busca e por LIKE em `chave` e `valor`, com as palavras da fala
            # atual. Nao e busca semantica - "carro" nao acha "automovel" - e
            # essa limitacao esta aceita de proposito: FTS5 ou embeddings
            # exigiriam indice novo ou modelo carregado, e o Notebook ja roda
            # YOLO, reconhecimento facial e Whisper em 7,6 GB (o Ollama local
            # foi removido justamente por travar essa maquina). Comecar por
            # LIKE entrega valor hoje sem gastar RAM; se faltar, o proximo
            # passo e FTS5 no MESMO arquivo, ainda sem processo novo.
            #
            # Os relevantes vem primeiro; o resto completa por data, para o
            # robo nunca ficar sem nenhum fato so porque a pergunta nao casou
            # com palavra nenhuma.
            conhecimento = self._conhecimento_relevante(sobre)
            return {
                "pessoa": pessoa,
                "conversas_recentes": list(reversed(conversas)),
                "conhecimento_relevante": conhecimento,
            }

        async with self._db.lock:
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

        async with self._db.lock:
            estatisticas = await asyncio.to_thread(_contar)
        await self._event_bus.publish("memory.recall_executed", {"categoria": "stats"})
        return estatisticas
