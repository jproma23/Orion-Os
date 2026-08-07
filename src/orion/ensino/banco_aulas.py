"""O BANCO DE AULAS - o conteudo que o robo ensina, sem IA nenhuma.

Decisao do dono em 2026-08-07: *"quero assim nao usar ia quero ter um banco
de dados com todos conteudo"*, e a cascata: *"so iremos consultar ia quando
ele nao tiver a informacao e deve buscar na internet"*.

Entao dar aula NAO chama modelo. A fala e o quadro de cada passo estao
escritos em arquivo, e o robo le. Isso muda tres coisas de uma vez:

- **custa zero de cota.** A cota do Gemini deste projeto ja acabou as 10h da
  manha de um dia (500 chamadas), e aula e justamente o uso que mais gastaria;
- **a aula e sempre a mesma.** Um modelo explicaria Bhaskara diferente a cada
  vez, e as vezes errado. Conteudo de ensino tem que ser conferivel;
- **funciona sem internet.**

## Fonte de verdade: os arquivos. O banco e indice.

    content/aulas/*.yaml   <- o conteudo, versionado no git, revisavel
    aulas.db               <- indice construido a partir deles

Mesma divisao do `mission_memory.db`: o que se recupera com um comando fica
fora do backup, e o que nao se recupera fica no `orion.db`. Se o indice
sumir, o conserto e `tools/aulas.py construir`. E como o conteudo e arquivo
de texto no repositorio, acrescentar aula NAO exige mexer em codigo.

## O que e conferido na CONSTRUCAO, e nao na aula

Todo bloco de quadro passa por `quadro.montar` na hora de indexar. Tipo
escrito errado ou campo faltando derruba o `construir` - com o nome do
arquivo e o numero do passo. A alternativa seria descobrir na frente de quem
esta aprendendo, que e o pior lugar possivel.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from orion.display import quadro as _quadro

logger = logging.getLogger("orion.ensino.banco_aulas")

#: Os tres niveis, na ordem em que se sobe. Pedido do dono: "quero as aulas
#: nos modos basico intermediario e avancado". Fechado de proposito - "meio
#: avancado" e "iniciante" viriam a existir no primeiro arquivo distraido, e
#: dai o robo teria niveis que a busca nao conhece.
NIVEIS = ("basico", "intermediario", "avancado")


#: Nomes que a pessoa fala, mapeados para o nivel. A fala e "aula avancada",
#: nao "nivel avancado".
_APELIDOS_NIVEL = {
    "basico": "basico", "basica": "basico", "iniciante": "basico",
    "comecando": "basico", "facil": "basico", "simples": "basico",
    "intermediario": "intermediario", "intermediaria": "intermediario",
    "medio": "intermediario", "media": "intermediario",
    "avancado": "avancado", "avancada": "avancado", "dificil": "avancado",
    "profundo": "avancado", "aprofundado": "avancado",
}


def caminho_padrao(raiz: Path) -> Path:
    """Onde mora o `saber.db` nesta maquina.

    O SSD e do Pi; o professor roda no Notebook, que nao o tem montado. Como
    o banco e INDICE - reconstruivel de `content/aulas/*.yaml`, que o git
    sincroniza - cada maquina constroi o seu, e nao ha copia de banco entre
    elas. Uma funcao so para nao existirem dois palpites diferentes sobre
    onde o arquivo esta (o comando construir e o robo TEM que concordar,
    senao ele le um banco vazio e diz que nao sabe a materia).
    """
    ssd = Path("/mnt/ssd/orion")
    return (ssd if ssd.is_dir() else raiz / "data") / "saber.db"


class AulaInvalida(ValueError):
    """Arquivo de aula malformado. Sempre com o nome do arquivo."""


@dataclass(frozen=True)
class Passo:
    """Um passo da aula: o que ele DIZ e o que ele ESCREVE, juntos.

    Os dois no mesmo objeto porque e assim que um professor ensina - a frase
    e o que vai ao quadro nascem juntas. Separa-los criaria duas listas que
    alguem teria que manter alinhadas na mao.
    """

    fala: str
    bloco: dict | None = None


@dataclass(frozen=True)
class Aula:
    id: str
    materia: str
    titulo: str
    nivel: str
    resumo: str
    passos: tuple[Passo, ...] = field(default_factory=tuple)

    @property
    def quantos_passos(self) -> int:
        return len(self.passos)


def normalizar_nivel(texto: str | None) -> str | None:
    """Descobre o nivel dentro de uma frase falada. None = nao foi dito."""
    if not texto:
        return None
    palavras = _sem_acento(texto.lower()).split()
    for palavra in palavras:
        nivel = _APELIDOS_NIVEL.get(palavra.strip(".,!?"))
        if nivel:
            return nivel
    return None


def _sem_acento(texto: str) -> str:
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def carregar_arquivo(caminho: Path) -> Aula:
    """Le um YAML de aula e o valida por inteiro.

    Levanta `AulaInvalida` com o arquivo e o passo. Erro de conteudo tem que
    ser encontravel por quem escreveu o conteudo - que pode nao ser quem
    escreve o codigo.
    """
    try:
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as erro:
        raise AulaInvalida(f"{caminho.name}: YAML invalido - {erro}") from erro

    for campo in ("id", "materia", "titulo", "nivel", "passos"):
        if not dados.get(campo):
            raise AulaInvalida(f"{caminho.name}: falta o campo '{campo}'")

    nivel = _sem_acento(str(dados["nivel"]).lower().strip())
    if nivel not in NIVEIS:
        raise AulaInvalida(
            f"{caminho.name}: nivel {dados['nivel']!r} nao existe - use um de {list(NIVEIS)}"
        )

    passos: list[Passo] = []
    for numero, bruto in enumerate(dados["passos"], start=1):
        fala = (bruto.get("fala") or "").strip()
        if not fala:
            raise AulaInvalida(f"{caminho.name}: passo {numero} sem fala")
        bloco = bruto.get("quadro")
        if bloco is not None:
            campos = {k: v for k, v in bloco.items() if k != "tipo"}
            try:
                # A conferencia que importa: o quadro sabe desenhar isto?
                bloco = _quadro.montar(bloco.get("tipo", ""), **campos)
            except _quadro.BlocoInvalido as erro:
                raise AulaInvalida(f"{caminho.name}: passo {numero} - {erro}") from erro
        passos.append(Passo(fala=fala, bloco=bloco))

    return Aula(
        id=str(dados["id"]).strip(),
        materia=_sem_acento(str(dados["materia"]).lower().strip()),
        titulo=str(dados["titulo"]).strip(),
        nivel=nivel,
        resumo=(dados.get("resumo") or "").strip(),
        passos=tuple(passos),
    )


def carregar_pasta(pasta: Path) -> list[Aula]:
    aulas = [carregar_arquivo(caminho) for caminho in sorted(pasta.glob("*.yaml"))]
    vistos: set[str] = set()
    for aula in aulas:
        if aula.id in vistos:
            raise AulaInvalida(f"id repetido entre arquivos: {aula.id!r}")
        vistos.add(aula.id)
    return aulas


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS aulas (
    id       TEXT PRIMARY KEY,
    materia  TEXT NOT NULL,
    titulo   TEXT NOT NULL,
    nivel    TEXT NOT NULL,
    resumo   TEXT NOT NULL DEFAULT '',
    passos   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aulas_materia ON aulas(materia, nivel);

-- FTS5, e nao banco vetorial: sao dezenas de aulas, nao milhoes. Um banco
-- vetorial somaria servidor e modelo de embedding na RAM do Notebook, que e
-- a mesma maquina que roda YOLO, reconhecimento facial e Whisper - e o
-- Ollama local ja foi removido daqui por travar exatamente isso.
--
-- `remove_diacritics 2` para "matematica" achar "matemática" nos dois
-- sentidos: quem fala nao digita acento, e quem escreveu o conteudo digitou.
CREATE VIRTUAL TABLE IF NOT EXISTS aulas_fts USING fts5(
    id UNINDEXED, titulo, materia, resumo, corpo,
    tokenize = "unicode61 remove_diacritics 2"
);
"""


class BancoAulas:
    """Indice de aulas em SQLite. Leitura barata, construcao rara."""

    def __init__(self, caminho: Path | str) -> None:
        self._caminho = Path(caminho)
        self._conexao: sqlite3.Connection | None = None

    def _conectar(self) -> sqlite3.Connection:
        if self._conexao is None:
            self._caminho.parent.mkdir(parents=True, exist_ok=True)
            self._conexao = sqlite3.connect(self._caminho)
            self._conexao.row_factory = sqlite3.Row
            self._conexao.executescript(_ESQUEMA)
        return self._conexao

    def fechar(self) -> None:
        if self._conexao is not None:
            self._conexao.close()
            self._conexao = None

    def construir(self, aulas: list[Aula]) -> int:
        """Reconstroi o indice do zero. Devolve quantas aulas entraram.

        Do ZERO e nao incremental: o indice e derivado dos arquivos, e aula
        apagada de `content/` tem que sumir daqui tambem. Indice incremental
        guardaria conteudo que ninguem mais consegue explicar de onde veio.
        """
        conexao = self._conectar()
        with conexao:
            conexao.execute("DELETE FROM aulas")
            conexao.execute("DELETE FROM aulas_fts")
            for aula in aulas:
                passos = json.dumps(
                    [{"fala": p.fala, "bloco": p.bloco} for p in aula.passos],
                    ensure_ascii=False,
                )
                conexao.execute(
                    "INSERT INTO aulas (id, materia, titulo, nivel, resumo, passos)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (aula.id, aula.materia, aula.titulo, aula.nivel, aula.resumo, passos),
                )
                # O corpo indexado inclui a FALA de cada passo: quem pergunta
                # "aula sobre bhaskara" nao sabe o titulo da aula, sabe a
                # palavra que ouviu. Buscar so no titulo perderia isso.
                corpo = " ".join(p.fala for p in aula.passos)
                conexao.execute(
                    "INSERT INTO aulas_fts (id, titulo, materia, resumo, corpo)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (aula.id, aula.titulo, aula.materia, aula.resumo, corpo),
                )
        return len(aulas)

    def buscar(self, pedido: str, nivel: str | None = None, limite: int = 5) -> list[Aula]:
        """Acha aulas pelo que a pessoa falou. Sem os passos - so o cabecalho.

        O titulo pesa mais que o corpo (bm25 com peso), pela mesma razao da
        Mission Memory: quem pede "aula de ingles" quer a aula CHAMADA ingles,
        nao a de matematica que menciona ingles de passagem.
        """
        termos = _termos_fts(pedido)
        if not termos:
            return []
        conexao = self._conectar()
        sql = (
            "SELECT a.* FROM aulas_fts f JOIN aulas a ON a.id = f.id"
            " WHERE aulas_fts MATCH ?"
        )
        parametros: list = [termos]
        if nivel:
            sql += " AND a.nivel = ?"
            parametros.append(nivel)
        sql += " ORDER BY bm25(aulas_fts, 0.0, 10.0, 5.0, 3.0, 1.0) LIMIT ?"
        parametros.append(limite)
        try:
            linhas = conexao.execute(sql, parametros).fetchall()
        except sqlite3.OperationalError:
            # Consulta que o FTS5 nao entende (aspas soltas, operador no
            # meio). Nao ha o que consertar aqui - nao achar e a resposta
            # honesta, e quem chama cai para o proximo degrau da cascata.
            logger.warning("busca de aula rejeitada pelo FTS5: %r", termos)
            return []
        return [self._montar(linha, com_passos=False) for linha in linhas]

    def carregar(self, id_aula: str) -> Aula | None:
        conexao = self._conectar()
        linha = conexao.execute("SELECT * FROM aulas WHERE id = ?", (id_aula,)).fetchone()
        return self._montar(linha, com_passos=True) if linha else None

    def listar(self, materia: str | None = None, nivel: str | None = None) -> list[Aula]:
        conexao = self._conectar()
        sql = "SELECT * FROM aulas WHERE 1=1"
        parametros: list = []
        if materia:
            sql += " AND materia = ?"
            parametros.append(_sem_acento(materia.lower()))
        if nivel:
            sql += " AND nivel = ?"
            parametros.append(nivel)
        sql += " ORDER BY materia, nivel, titulo"
        return [
            self._montar(linha, com_passos=False)
            for linha in conexao.execute(sql, parametros).fetchall()
        ]

    @staticmethod
    def _montar(linha: sqlite3.Row, *, com_passos: bool) -> Aula:
        passos: tuple[Passo, ...] = ()
        if com_passos:
            passos = tuple(
                Passo(fala=p["fala"], bloco=p.get("bloco"))
                for p in json.loads(linha["passos"])
            )
        return Aula(
            id=linha["id"],
            materia=linha["materia"],
            titulo=linha["titulo"],
            nivel=linha["nivel"],
            resumo=linha["resumo"],
            passos=passos,
        )


#: Palavras que aparecem no pedido e nao ajudam a achar nada. Sem tirar isto,
#: "me da uma aula de ingles" traz qualquer aula que contenha a palavra
#: "aula" - ou seja, todas.
_VAZIAS = frozenset({
    "me", "da", "de", "do", "uma", "um", "o", "a", "os", "as", "por",
    "favor", "quero", "queria", "gostaria", "aula", "aulas", "ensina",
    "ensinar", "aprender", "sobre", "com", "para", "pra", "no", "na", "em",
    "que", "e", "vamos", "fala", "falar", "mostra", "mostrar", "pode",
    "poderia", "modo", "nivel", "seu", "sua", "voce",
})


def _termos_fts(pedido: str) -> str:
    """Transforma a frase falada em consulta FTS5.

    Cada palavra vira um prefixo (`ingles*`) unido por OR: quem fala nao
    acerta a forma exata da palavra do conteudo ("equacoes" contra
    "equacao"), e exigir todas as palavras nao acharia nada quase sempre.
    As aspas duplas sao retiradas porque elas mudam o significado da
    consulta no FTS5 - e vindo de fala, sao sempre engano.
    """
    limpo = _sem_acento(pedido.lower()).replace('"', " ")
    palavras = [
        p for p in "".join(c if c.isalnum() else " " for c in limpo).split()
        if len(p) > 2 and p not in _VAZIAS and p not in _APELIDOS_NIVEL
    ]
    if not palavras:
        return ""
    alternativas = " OR ".join(f"{p}*" for p in palavras)
    # SO nas colunas curadas - titulo, materia e resumo. O `corpo` (todas as
    # falas da aula) fica indexado, mas fora da busca.
    #
    # Por que: em uso real, "podia me dar uma aula de ciencia, por exemplo"
    # devolveu a aula de EQUACAO DO SEGUNDO GRAU. O que casou foi a palavra
    # "exemplo", que aparece no meio da explicacao de Bhaskara. Buscar no
    # corpo faz qualquer palavra solta de uma frase encostar em qualquer
    # aula - e num acervo pequeno, o menos ruim vira resposta.
    #
    # Teto de bm25 NAO resolveria: medido, "aula de ingles" pontua -0.0
    # porque "ingles" esta em metade do acervo, enquanto o encontrao com
    # "exemplo" pontuava melhor. Nota nao separa o certo do errado aqui;
    # ONDE casou, sim.
    return f"{{titulo materia resumo}} : ({alternativas})"
