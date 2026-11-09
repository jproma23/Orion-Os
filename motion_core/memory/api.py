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
from datetime import datetime, timedelta
from typing import Any

from motion_core.memory import saber
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
    "conhecimentos": ("criado_em", "atualizado_em"),
    "experiencias": ("criado_em",),
    "procedimentos": ("criado_em",),
    "dispositivos": ("criado_em", "atualizado_em"),
    "relacoes": ("criado_em",),
    "missoes": ("inicio",),
    "eventos": ("timestamp",),
}

#: Coluna "tocada" automaticamente em update(), quando a tabela tem uma.
_CAMPO_ATUALIZACAO_AUTOMATICO: dict[str, str] = {
    "pessoas": "atualizado_em",
    "conhecimentos": "atualizado_em",
    "dispositivos": "atualizado_em",
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
        de `filtro`, mais recentes primeiro, limitados a `limite`.

        FILTRAR POR `None` SIGNIFICA "COLUNA VAZIA" (corrigido em 2026-08-04).

        Ate hoje esta funcao montava `coluna = ?` para todo filtro, inclusive
        quando o valor era `None` - e em SQL `coluna = NULL` **nunca casa com
        linha nenhuma**, nem com as que estao vazias. Comparar com NULL da
        sempre NULL, que nao e verdadeiro. O jeito certo e `IS NULL`.

        O ESTRAGO REAL: os recados. `RecadosPendentes.pendentes()` busca com
        `{"entregue_em": None}` - "recados que ainda nao foram entregues" - e
        recebia lista vazia SEMPRE. O dono pediu ao Fofao para avisar a Marah,
        o robo guardou o recado, encontrou a pessoa, cumprimentou... e nao
        tinha o que entregar, porque a consulta nunca achava nada. O recurso
        inteiro nunca funcionou desde que nasceu.

        E nada denunciava: lista vazia e resposta legitima de `recall`, e
        indistinguivel de "nao ha recado pendente". Sem erro, sem excecao, sem
        teste vermelho - o mesmo formato do evento publicado no vazio.
        """
        tabela = self._tabela(categoria)
        filtro = filtro or {}

        def _buscar() -> list[dict[str, Any]]:
            self._validar_colunas(tabela, filtro.keys())
            condicoes: list[str] = []
            valores: list[Any] = []
            for coluna, valor in filtro.items():
                if valor is None:
                    # Sem parametro: `IS NULL` nao aceita placeholder.
                    condicoes.append(f"{coluna} IS NULL")
                else:
                    condicoes.append(f"{coluna} = ?")
                    valores.append(valor)
            condicao = " AND ".join(condicoes) or "1=1"
            sql = f"SELECT * FROM {tabela} WHERE {condicao} ORDER BY id DESC LIMIT ?"
            linhas = self._db.conexao.execute(sql, (*valores, limite)).fetchall()
            return [dict(linha) for linha in linhas]

        async with self._db.lock:
            resultado = await asyncio.to_thread(_buscar)
        await self._event_bus.publish(
            "memory.recall_executed",
            {"categoria": categoria, "filtro": filtro, "quantidade": len(resultado)},
        )
        return resultado

    async def contar_repeticoes(
        self, situacao: str, resultado: str, janela_s: float
    ) -> int:
        """Quantas experiencias da situacao com aquele resultado aconteceram
        na janela de tempo (`criado_em` >= agora - janela).

        O degrau do ciclo fechado da navegacao (Cap 12) usa este numero para
        responder "a mesma situacao ja falhou varias vezes?" antes de repetir
        um erro - o recall ESTRUTURADO (situacao/resultado/recencia), em vez
        de busca por texto no FTS, porque aqui o que importa e contar
        desfechos iguais, nao achar uma frase parecida.

        `criado_em` e ISO-UTC em todas as categorias (agora_iso), entao a
        comparacao de strings e lexicografica e correta.
        """
        desde = agora_iso()
        # A janela usa o relogio do caller: subtrair aqui com UTC mantem o
        # mesmo fuso do que o banco gravou. Ver contar_repeticoes nos testes.
        desde_iso = (
            datetime.fromisoformat(desde) - timedelta(seconds=janela_s)
        ).isoformat()

        def _contar() -> int:
            return self._db.conexao.execute(
                "SELECT COUNT(*) FROM experiencias "
                "WHERE situacao = ? AND resultado = ? AND criado_em >= ?",
                (situacao, resultado, desde_iso),
            ).fetchone()[0]

        async with self._db.lock:
            contagem = await asyncio.to_thread(_contar)
        await self._event_bus.publish(
            "memory.recall_executed",
            {"categoria": "experiencias", "situacao": situacao, "quantidade": contagem},
        )
        return contagem

    async def experiencias_recentes(self, situacao: str, limite: int = 5) -> list[dict[str, Any]]:
        """As ultimas experiencias de uma situacao, da mais nova para a mais velha.

        Para a IA decidir com o que JA aconteceu (08/11, dono: "estabelecer
        contexto para a IA aprender"): o seguir pergunta "o que faco agora?"
        levando junto como foram as ultimas vezes.
        """

        def _ler() -> list[dict[str, Any]]:
            linhas = self._db.conexao.execute(
                "SELECT o_que_aconteceu, o_que_funcionou, o_que_falhou, resultado, criado_em "
                "FROM experiencias WHERE situacao = ? ORDER BY id DESC LIMIT ?",
                (situacao, int(limite)),
            ).fetchall()
            return [dict(linha) for linha in linhas]

        async with self._db.lock:
            return await asyncio.to_thread(_ler)

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

    async def saber_guardar(self, categoria: str, dados: dict[str, Any]) -> int:
        """Grava uma entrada da memoria cognitiva e indexa o texto no FTS.

        `categoria` e uma das 5 (conhecimentos, experiencias, procedimentos,
        dispositivos, relacoes) - ver `saber.py`. Alem da linha na tabela,
        atualiza `saber_fts` para o "Eu ja sei?" achar depois. Publica o
        mesmo evento de mutacao das outras gravacoes.
        """
        tabela = self._tabela(categoria)
        completos = dict(dados)
        if "criado_em" not in completos and categoria in {
            "conhecimentos", "experiencias", "procedimentos", "dispositivos", "relacoes",
        }:
            completos["criado_em"] = agora_iso()
        if categoria == "conhecimentos" and "atualizado_em" not in completos:
            completos["atualizado_em"] = completos["criado_em"]
        if categoria == "dispositivos" and "atualizado_em" not in completos:
            completos["atualizado_em"] = completos["criado_em"]

        def _inserir() -> int:
            self._validar_colunas(tabela, completos.keys())
            colunas = ", ".join(completos.keys())
            marcadores = ", ".join("?" for _ in completos)
            cursor = self._db.conexao.execute(
                f"INSERT INTO {tabela} ({colunas}) VALUES ({marcadores})",
                tuple(completos.values()),
            )
            novo_id = cursor.lastrowid
            saber.sincronizar_fts(self._db.conexao, categoria, novo_id, completos)
            return novo_id

        async with self._db.lock:
            novo_id = await asyncio.to_thread(_inserir)
        await self._event_bus.publish(
            "memory.updated", {"categoria": categoria, "operacao": "saber_guardar", "id": novo_id}
        )
        return novo_id

    async def saber_atualizar(self, categoria: str, ref_id: int, dados: dict[str, Any]) -> None:
        """Regrava campos de uma entrada da memoria cognitiva e reindexa o FTS.

        Existe desde 2026-10-25: o nome do robo mudou para Jarvis e o
        conhecimento ja gravado continuava dizendo "Fofao" - o
        `saber_guardar` so INSERE, e a alimentacao pulava o que ja existia.
        Atualiza `atualizado_em` onde a tabela tem a coluna.
        """
        tabela = self._tabela(categoria)
        completos = dict(dados)
        if categoria in {"conhecimentos", "dispositivos"} and "atualizado_em" not in completos:
            completos["atualizado_em"] = agora_iso()

        def _atualizar() -> None:
            self._validar_colunas(tabela, completos.keys())
            atribuicoes = ", ".join(f"{coluna} = ?" for coluna in completos)
            self._db.conexao.execute(
                f"UPDATE {tabela} SET {atribuicoes} WHERE id = ?",
                (*completos.values(), ref_id),
            )
            linha = self._db.conexao.execute(
                f"SELECT * FROM {tabela} WHERE id = ?", (ref_id,)
            ).fetchone()
            if linha is not None:
                saber.sincronizar_fts(self._db.conexao, categoria, ref_id, dict(linha))

        async with self._db.lock:
            await asyncio.to_thread(_atualizar)
        await self._event_bus.publish(
            "memory.updated", {"categoria": categoria, "operacao": "saber_atualizar", "id": ref_id}
        )

    async def saber_buscar(self, consulta: str, limite: int = 3) -> list[dict[str, Any]]:
        """O que o ORION ja sabe que cobre a fala - o portao do "Eu ja sei?".

        A busca roda no `saber_fts`; o achado e completado com a linha inteira
        da tabela de origem (via `saber.carregar`). Vazio = ele nao sabe
        (e a conversa segue para a IA - a consultora externa).

        `self` (sem lock) ja e a regra dos metodos de consulta junto com o
        resto da API: ler dentro do lock evita resolver a linha numa
        metade-escrita.
        """
        def _buscar() -> list[dict[str, Any]]:
            achados = saber.buscar(self._db.conexao, consulta, limite=limite)
            completos: list[dict[str, Any]] = []
            for achado in achados:
                registro = saber.carregar(
                    self._db.conexao,
                    str(achado.get("tipo_fonte", "")),
                    int(achado.get("ref_id", 0)),
                )
                if registro is None:
                    continue
                registro["nota_bm25"] = achado.get("nota")
                completo = dict(registro)
                completo["categoria"] = {
                    "conhecimento": "conhecimentos",
                    "experiencia": "experiencias",
                    "procedimento": "procedimentos",
                }.get(str(achado.get("tipo_fonte", "")), "conhecimentos")
                completos.append(completo)
            return completos

        async with self._db.lock:
            resultado = await asyncio.to_thread(_buscar)
        await self._event_bus.publish(
            "memory.recall_executed",
            {"categoria": "saber_buscar", "consulta": consulta, "quantidade": len(resultado)},
        )
        return resultado

    async def saber_stats(self) -> dict[str, Any]:
        """Contagens da memoria cognitiva + consultas a IA.

        Alimenta o indicador CONHECIMENTO ORION: quantos conhecimentos,
        experiencias, procedimentos, dispositivos, relacoes - e quantas vezes
        a IA foi consultada (a medida de quanto ele ainda esta dependente).
        """

        def _levantar() -> dict[str, Any]:
            contagens = saber.contar(self._db.conexao)
            linha = self._db.conexao.execute(
                "SELECT CAST(valor AS INTEGER) AS n FROM configuracao_memoria "
                "WHERE chave = ?",
                ("estatisticas.consultas_ia",),
            ).fetchone()
            contagens["consultas_ia"] = int(linha["n"] if linha is not None else 0)
            contagens["total_conhecimento_local"] = sum(
                contagens[c]
                for c in (
                    "conhecimentos", "experiencias", "procedimentos",
                    "dispositivos", "relacoes",
                )
            )
            return contagens

        async with self._db.lock:
            dados = await asyncio.to_thread(_levantar)
        await self._event_bus.publish("memory.recall_executed", {"categoria": "saber_stats"})
        return dados

    async def registrar_consulta_ia(self) -> None:
        """Conta mais uma consulta a IA (a consultora externa).

        Chamado quando a conversa efetivamente caiu no modelo - o contador e
        a medida de dependencia: quanto menor, mais ele tem conhecimento
        proprio. Gravado em `configuracao_memoria` como inteiro em texto,
        incrementado atomicamente para nao perder contagem por corrida.
        """

        def _incrementar() -> None:
            self._db.conexao.execute(
                "INSERT INTO configuracao_memoria (chave, valor) VALUES (?, '1') "
                "ON CONFLICT(chave) DO UPDATE SET valor = "
                "CAST(CAST(configuracao_memoria.valor AS INTEGER) + 1 AS TEXT)",
                ("estatisticas.consultas_ia",),
            )

        async with self._db.lock:
            await asyncio.to_thread(_incrementar)

