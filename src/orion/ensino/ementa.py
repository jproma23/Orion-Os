"""EMENTAS - o mapa de tudo o que o robo ensina (Cap 13, ensino).

Pedido do dono em 2026-08-07: *"criem ementas para todos os cursos com todo
conteudo"*, *"com plano de aula"*.

## O que a ementa e, e o que ela NAO e

A ementa e o **mapa**: quais assuntos existem em cada materia, em que nivel,
e em que ordem se aprende. A aula e o **territorio**: a fala passo a passo e
o que vai ao quadro.

Os dois sao separados de proposito, e a separacao resolve um problema real:
o mapa cabe inteiro numa sessao de trabalho, o territorio nao. Com a ementa
escrita antes das aulas, o robo consegue dizer **o que ele ainda nao sabe** -
que e diferente de nao ter nada a dizer:

    "De matematica eu sei dar equacao do segundo grau. Estao previstas mais
     onze, que eu ainda nao aprendi."

Um topico que aponta para uma aula inexistente NAO e erro: e o estado normal
de uma ementa em construcao, e e justamente o que diz o que falta escrever.

## A cobertura e um numero, e ele aparece

`tools/aulas.py construir` imprime quantos topicos tem aula escrita. Sem esse
numero, "criar ementas para todos os cursos" viraria uma pilha de arquivos
sem ninguem saber quanto do combinado existe de fato - e este projeto tem
historico de peca pronta que ninguem chama.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from orion.ensino.banco_aulas import NIVEIS

logger = logging.getLogger("orion.ensino.ementa")


class EmentaInvalida(ValueError):
    pass


@dataclass(frozen=True)
class Topico:
    id: str
    titulo: str
    conteudo: str = ""
    #: Id da aula que ensina este topico. Vazio = prevista e nao escrita.
    aula: str = ""


@dataclass(frozen=True)
class Ementa:
    materia: str
    nome: str
    descricao: str
    #: nivel -> topicos, na ordem de aprendizado.
    topicos: dict[str, tuple[Topico, ...]] = field(default_factory=dict)

    @property
    def quantos_topicos(self) -> int:
        return sum(len(t) for t in self.topicos.values())

    @property
    def quantas_aulas(self) -> int:
        return sum(1 for lista in self.topicos.values() for t in lista if t.aula)


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )


def carregar_arquivo(caminho: Path) -> Ementa:
    try:
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as erro:
        # Erro de conteudo tem que ser legivel para quem escreve CONTEUDO, e
        # nao um traceback de 40 linhas. O caso real: dois-pontos no meio de
        # uma frase sem aspas, que em YAML abre um mapa.
        raise EmentaInvalida(f"{caminho.name}: YAML invalido - {erro}") from erro
    for campo in ("materia", "nome", "niveis"):
        if not dados.get(campo):
            raise EmentaInvalida(f"{caminho.name}: falta '{campo}'")

    topicos: dict[str, tuple[Topico, ...]] = {}
    vistos: set[str] = set()
    for nivel, secao in (dados["niveis"] or {}).items():
        nivel_limpo = _sem_acento(str(nivel))
        if nivel_limpo not in NIVEIS:
            raise EmentaInvalida(
                f"{caminho.name}: nivel {nivel!r} nao existe - use um de {list(NIVEIS)}"
            )
        lista = []
        for bruto in (secao or {}).get("topicos") or []:
            if not bruto.get("id") or not bruto.get("titulo"):
                raise EmentaInvalida(f"{caminho.name}/{nivel}: topico sem id ou titulo")
            if bruto["id"] in vistos:
                raise EmentaInvalida(f"{caminho.name}: topico repetido {bruto['id']!r}")
            vistos.add(bruto["id"])
            lista.append(
                Topico(
                    id=str(bruto["id"]),
                    titulo=str(bruto["titulo"]).strip(),
                    conteudo=" ".join(str(bruto.get("conteudo") or "").split()),
                    aula=str(bruto.get("aula") or "").strip(),
                )
            )
        topicos[nivel_limpo] = tuple(lista)

    if not any(topicos.values()):
        raise EmentaInvalida(f"{caminho.name}: nenhum topico")

    return Ementa(
        materia=_sem_acento(str(dados["materia"])),
        nome=str(dados["nome"]).strip(),
        descricao=" ".join(str(dados.get("descricao") or "").split()),
        topicos=topicos,
    )


def carregar_pasta(pasta: Path) -> list[Ementa]:
    return [carregar_arquivo(caminho) for caminho in sorted(pasta.glob("*.yaml"))]


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS ementas (
    materia TEXT PRIMARY KEY, nome TEXT NOT NULL,
    descricao TEXT NOT NULL DEFAULT '', topicos TEXT NOT NULL
);
"""


class BancoEmentas:
    """As ementas no mesmo `saber.db` das aulas.

    Sem FTS aqui: a ementa e consultada por MATERIA, que e uma palavra so e
    ja vem do que a pessoa falou. Indice de texto completo seria maquinario
    para uma busca que e um `WHERE materia = ?`.
    """

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

    def construir(self, ementas: list[Ementa]) -> int:
        conexao = self._conectar()
        with conexao:
            conexao.execute("DELETE FROM ementas")
            for ementa in ementas:
                conexao.execute(
                    "INSERT INTO ementas (materia, nome, descricao, topicos) VALUES (?,?,?,?)",
                    (
                        ementa.materia, ementa.nome, ementa.descricao,
                        json.dumps(
                            {
                                nivel: [
                                    {"id": t.id, "titulo": t.titulo,
                                     "conteudo": t.conteudo, "aula": t.aula}
                                    for t in lista
                                ]
                                for nivel, lista in ementa.topicos.items()
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
        return len(ementas)

    def carregar(self, materia: str) -> Ementa | None:
        linha = self._conectar().execute(
            "SELECT * FROM ementas WHERE materia = ?", (_sem_acento(materia),)
        ).fetchone()
        return self._montar(linha) if linha else None

    def materias(self) -> list[Ementa]:
        return [
            self._montar(linha)
            for linha in self._conectar().execute("SELECT * FROM ementas ORDER BY nome")
        ]

    @staticmethod
    def _montar(linha: sqlite3.Row) -> Ementa:
        bruto = json.loads(linha["topicos"])
        return Ementa(
            materia=linha["materia"],
            nome=linha["nome"],
            descricao=linha["descricao"],
            topicos={
                nivel: tuple(Topico(**t) for t in lista) for nivel, lista in bruto.items()
            },
        )


#: Como se pergunta pelo programa do curso. Palavra inteira, sempre - a
#: licao de "parar" dentro de "preparar" vale aqui tambem.
_PADRAO_EMENTA = re.compile(
    r"\bo que voce ensina\b|\bo que voce sabe ensinar\b|\bquais aulas\b"
    r"|\bque aulas\b|\bquais materias\b|\bque materias\b|\bementa\b"
    r"|\bo que voce ensina de\b|\bconteudo programatico\b|\bo que voce pode ensinar\b"
)


def responder_pergunta(banco: BancoEmentas, pergunta: str, ids_de_aulas: set[str]) -> str | None:
    """'O que voce ensina?' respondido do mapa, sem modelo nenhum.

    Diz o que ele ENSINA e o que ainda NAO SABE, com numero. Um robo que
    lista 312 topicos como se soubesse todos estaria mentindo - e a lista
    honesta e mais util: ela diz o que pedir hoje e o que falta escrever.
    """
    limpo = _sem_acento(pergunta)
    if not _PADRAO_EMENTA.search(limpo):
        return None

    ementas = banco.materias()
    if not ementas:
        return None

    # Pediu de uma materia especifica?
    for ementa in ementas:
        if re.search(rf"\b{re.escape(ementa.materia)}\b", limpo):
            prontas = [
                t for lista in ementa.topicos.values() for t in lista
                if t.aula in ids_de_aulas
            ]
            total = ementa.quantos_topicos
            if not prontas:
                return (
                    f"De {ementa.nome} eu tenho {total} assuntos no meu programa, "
                    f"mas ainda nao aprendi nenhum deles direito. Nao vou fingir que sei."
                )
            titulos = ", ".join(t.titulo for t in prontas[:4])
            return (
                f"De {ementa.nome} eu ja sei dar {len(prontas)} de {total} assuntos - "
                f"{titulos}. O resto esta no meu programa e eu ainda preciso aprender."
            )

    nomes = ", ".join(e.nome for e in ementas[:-1]) + f" e {ementas[-1].nome}"
    total = sum(e.quantos_topicos for e in ementas)
    prontas = sum(
        1 for e in ementas for lista in e.topicos.values() for t in lista
        if t.aula in ids_de_aulas
    )
    return (
        f"Meu programa tem {len(ementas)} materias - {nomes} - com {total} assuntos "
        f"ao todo, em tres niveis. Desses eu ja sei dar {prontas}; o resto eu ainda "
        f"preciso aprender. Pergunte de uma materia para eu dizer o que ja sei dela."
    )


def contar_cobertura(ementas: list[Ementa], ids_de_aulas: set[str]) -> tuple[int, int]:
    """Quantos topicos tem aula ESCRITA de verdade, do total.

    Confere contra as aulas que existem, e nao contra o campo `aula` do
    arquivo: um id escrito errado ali contaria como aula pronta e o numero
    mentiria - que e o oposto do que ele existe para fazer.
    """
    total = sum(e.quantos_topicos for e in ementas)
    prontos = sum(
        1
        for e in ementas
        for lista in e.topicos.values()
        for t in lista
        if t.aula and t.aula in ids_de_aulas
    )
    return prontos, total
