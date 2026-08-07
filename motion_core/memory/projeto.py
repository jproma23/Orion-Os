"""Leitura da Mission Memory - o indice que o projeto tem de si mesmo.

POR QUE ISTO MORA AQUI e nao em `tools/mission_memory.py`, onde nasceu: o
robo passou a CONSULTAR este indice em operacao (ponte `memory.*`), e
`motion_core/` e o runtime do Pi enquanto `tools/` e bancada. Runtime
dependendo de ferramenta de bancada inverte a direcao da dependencia e
quebra no dia em que alguem arrumar a pasta `tools/`.

A divisao ficou na costura natural:

    CONSTRUIR o indice  -> tools/mission_memory.py   (dev, roda a mao)
    LER o indice        -> aqui                      (runtime, o robo usa)

O arquivo do indice e ARTEFATO DERIVADO (`mission_memory.py construir`
refaz em segundos), por isso mora fora do orion.db, que guarda o que nao se
recupera.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

CAMINHO_PADRAO = Path("/mnt/ssd/orion/mission_memory.db")

#: `remove_diacritics 2` e o que faz "reconexao" achar "reconexão" e
#: vice-versa. Precisa ser IGUAL ao usado na construcao - tokenizador
#: diferente entre escrita e leitura devolve resultado silenciosamente
#: errado, sem erro nenhum.
TOKENIZADOR = "unicode61 remove_diacritics 2"

#: Peso por coluna no bm25. O titulo vale mais que o corpo: quem procura
#: "recado" quer a sessao QUE TRATA de recado, nao as dez que citam a
#: palavra de passagem. As tres primeiras colunas sao UNINDEXED e entram
#: com 0.
PESOS_BM25 = (0.0, 0.0, 0.0, 10.0, 1.0)

TIPOS = ("journal", "commit", "edr", "codigo")


@dataclass
class Achado:
    tipo: str
    ref: str
    data: str
    titulo: str
    trecho: str

    def como_dict(self) -> dict:
        """Formato que atravessa a ponte TCP ate o Notebook."""
        return {
            "tipo": self.tipo,
            "ref": self.ref,
            "data": self.data,
            "titulo": self.titulo,
            "trecho": self.trecho,
        }


def consulta_fts(pergunta: str) -> str:
    """Transforma a pergunta em consulta FTS5 sem explodir na sintaxe.

    Quem pergunta digita "reconexao serial", nao `"reconexao" AND "serial"`.
    Pior: aspas, hifen e parenteses soltos sao OPERADORES no FTS5 e derrubam
    a consulta com erro cru. Cada palavra vira termo literal entre aspas, e
    o conjunto e AND implicito.
    """
    palavras = re.findall(r"\w+", pergunta, flags=re.UNICODE)
    if not palavras:
        raise ValueError("pergunta sem nenhuma palavra buscavel")
    return " ".join(f'"{p}"' for p in palavras)


def buscar(
    caminho_db: Path, pergunta: str, limite: int = 5, tipo: str | None = None
) -> list[Achado]:
    if not caminho_db.exists():
        raise FileNotFoundError(
            f"indice nao existe em {caminho_db} - rode `tools/mission_memory.py construir`"
        )
    con = sqlite3.connect(f"file:{caminho_db}?mode=ro", uri=True)
    try:
        pesos = ", ".join(str(p) for p in PESOS_BM25)
        filtro = " AND tipo = ?" if tipo else ""
        parametros: list = [consulta_fts(pergunta)]
        if tipo:
            parametros.append(tipo)
        parametros.append(limite)
        linhas = con.execute(
            f"""
            SELECT tipo, ref, data, titulo,
                   snippet(documentos, 4, '>>', '<<', ' ... ', 24)
            FROM documentos
            WHERE documentos MATCH ?{filtro}
            ORDER BY bm25(documentos, {pesos})
            LIMIT ?
            """,
            parametros,
        ).fetchall()
    finally:
        con.close()
    return [Achado(*linha) for linha in linhas]


def estado(caminho_db: Path) -> list[tuple[str, int]]:
    if not caminho_db.exists():
        return []
    con = sqlite3.connect(f"file:{caminho_db}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT tipo, count(*) FROM documentos GROUP BY tipo ORDER BY tipo"
        ).fetchall()
    finally:
        con.close()
