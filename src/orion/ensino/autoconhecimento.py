"""AUTOCONHECIMENTO - o robo respondendo sobre si mesmo, sem IA.

Pedido do dono em 2026-08-07: *"ele tem q ter todos os conhecimentos sobre
ele como ele move as rodas a sua cabeca e tudo q ele tem"*.

## Por que isto NAO deve passar pelo modelo, nem quando ha cota

O modelo nao conhece este hardware. Perguntado sobre o proprio corpo, ele
INVENTA - e ha registro no proprio projeto: um catalogo trazido de fora
listava "comando_manipulacao (pegue, traga)" para um robo que **nao tem
bracos**. Sobre o proprio corpo, a fonte tem que ser o arquivo, e nunca um
palpite gerado.

Por isso este caminho vem ANTES da IA na cascata, e nao depois como o
comando local: nao e um substituto para quando a IA falta, e a resposta
CERTA. Um fato sobre o corpo do robo nao melhora sendo escrito por um modelo.

## Como acha

Mesmo FTS5 do banco de aulas, no mesmo `saber.db`. O texto indexado inclui
as `perguntas` do arquivo, que sao as formas de perguntar esperadas: quem
pergunta nao usa as palavras da resposta ("quantas rodas voce tem" nao
contem "motor", que e a palavra da explicacao).

## O portao, e por que ele e apertado

Pergunta sobre SI MESMO tem uma marca clara em portugues: fala de "voce",
"seu", "sua", "te". Sem esse portao, "como anda a obra" cairia na resposta
sobre as rodas - e sequestrar conversa e o defeito que fez o classificador
de intencao ser recusado neste projeto. Aqui o portao vem primeiro e a busca
depois.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

#: Mesmos sufixos de flexao do saber_buscar (motion_core/memory/saber.py).
_SUFIXOS_FLEXAO = ("", "s", "es", "a", "as", "o", "os")

logger = logging.getLogger("orion.ensino.autoconhecimento")

#: Marcas de que a pergunta e sobre ELE. Palavra inteira, sempre.
_PADRAO_SOBRE_ELE = re.compile(
    r"\bvoce\b|\bvc\b|\bteu\b|\btua\b|\bseu\b|\bsua\b|\bseus\b|\bsuas\b|\bte\b|\bcontigo\b"
)

#: Pontuacao minima do bm25 para a resposta valer. bm25 do SQLite e NEGATIVO
#: e quanto MENOR melhor; acima deste teto o achado e fraco demais e e melhor
#: nao responder do que responder outra coisa.
_TETO_BM25 = -0.5

#: Palavras que so armam a pergunta e nao dizem SOBRE O QUE ela e. Ficam de
#: fora da busca: elas aparecem nas perguntas indexadas de quase todo fato, e
#: fariam qualquer frase casar com alguma coisa.
_PALAVRAS_DE_PERGUNTA = frozenset({
    "qual", "quais", "como", "quando", "onde", "quanto", "quantos", "quantas",
    "que", "porque", "voce", "vcs", "seu", "sua", "seus", "suas", "teu", "tua",
    "para", "pra", "com", "tem", "esta", "sao", "faz", "fazer", "pode", "poderia",
    "mim", "meu", "minha", "isso", "aqui", "por", "dos", "das", "num", "numa",
    # "quem" e "sabe" entraram depois de "voce sabe quem descobriu o Brasil?"
    # ser respondida com o fato dos OLHOS - casou com a pergunta indexada
    # "como voce sabe quem eu sou". As duas armam a pergunta; o assunto dela
    # estava em "descobriu" e "Brasil".
    "quem", "sabe", "saber", "conhece", "explica", "explicar", "diz", "dizer",
    # "nao" entrou depois de "voce ainda nao tem essa capacidade" casar com o
    # TITULO "O que eu NAO tenho" (uso real, 08/08): negar nao diz sobre o
    # que se fala, e fez o fato dos BRACOS vencer sem a frase falar de braco.
    "nao",
})

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS sobre_mim (
    id TEXT PRIMARY KEY, titulo TEXT NOT NULL, resposta TEXT NOT NULL,
    perguntas TEXT NOT NULL DEFAULT '[]'
);
CREATE VIRTUAL TABLE IF NOT EXISTS sobre_mim_fts USING fts5(
    id UNINDEXED, titulo, perguntas, resposta,
    tokenize = "unicode61 remove_diacritics 2"
);
"""


@dataclass(frozen=True)
class Fato:
    id: str
    titulo: str
    resposta: str
    perguntas: tuple[str, ...] = ()


class FatoInvalido(ValueError):
    pass


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )


def carregar_arquivo(caminho: Path) -> list[Fato]:
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    fatos = []
    for bruto in dados.get("fatos") or []:
        for campo in ("id", "titulo", "resposta"):
            if not bruto.get(campo):
                raise FatoInvalido(f"{caminho.name}: fato sem '{campo}'")
        fatos.append(
            Fato(
                id=str(bruto["id"]),
                titulo=str(bruto["titulo"]).strip(),
                resposta=" ".join(str(bruto["resposta"]).split()),
                perguntas=tuple(str(p) for p in (bruto.get("perguntas") or [])),
            )
        )
    if not fatos:
        raise FatoInvalido(f"{caminho.name}: nenhum fato")
    return fatos


class Autoconhecimento:
    def __init__(self, caminho_banco: Path | str) -> None:
        self._caminho = Path(caminho_banco)
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

    def construir(self, fatos: list[Fato]) -> int:
        conexao = self._conectar()
        with conexao:
            conexao.execute("DELETE FROM sobre_mim")
            conexao.execute("DELETE FROM sobre_mim_fts")
            for fato in fatos:
                conexao.execute(
                    "INSERT INTO sobre_mim (id, titulo, resposta, perguntas) VALUES (?,?,?,?)",
                    (fato.id, fato.titulo, fato.resposta, json.dumps(list(fato.perguntas))),
                )
                conexao.execute(
                    "INSERT INTO sobre_mim_fts (id, titulo, perguntas, resposta) VALUES (?,?,?,?)",
                    (fato.id, fato.titulo, " ".join(fato.perguntas), fato.resposta),
                )
        return len(fatos)

    def responder(self, pergunta: str) -> str | None:
        """A resposta sobre si mesmo, ou None se nao for pergunta sobre ele.

        None em dois casos - nao e sobre ele, ou nao ha fato bom o bastante -
        e nos dois a frase segue para a conversa normal. Responder "quase
        isso" sobre o proprio corpo seria mentir com confianca.
        """
        normalizada = _sem_acento(pergunta)
        if not _PADRAO_SOBRE_ELE.search(normalizada):
            return None
        # SO PALAVRAS DE CONTEUDO. Sem isto, "qual e a sua comida preferida"
        # casava com a resposta sobre a VOZ - porque "qual" e "sua" aparecem
        # nas perguntas indexadas de varios fatos, e uma delas ganhava por
        # acaso. Palavra de pergunta nao diz sobre O QUE se pergunta.
        #
        # SEM DUPLICATA (2026-08-19). Achado no chat real: "As rodas, use suas
        # rodas para se locomover" e "Deixe sua roda esquerda e depois diga a
        # roda direita" eram respondidas com a palestra sobre as rodas - a
        # palavra "rodas" repetida inflava a cobertura para duas e passava no
        # `min(2, len(palavras))` abaixo, devolvendo o COMANDO como pergunta
        # sobre o corpo. Uma palavra repetida nao e um segundo assunto.
        palavras = list(dict.fromkeys(
            p for p in re.sub(r"[^\w\s]", " ", normalizada).split()
            if len(p) > 2 and p not in _PALAVRAS_DE_PERGUNTA
        ))
        if not palavras:
            return None
        # SO nas colunas curadas: o titulo e as PERGUNTAS esperadas, nunca a
        # resposta. Achado em uso real (07/08): "voce me explica porque o ceu
        # e azul?" foi sequestrado pelo fato da TELA, porque a palavra
        # "explicando" aparece no meio da resposta dele. A pergunta tem
        # "voce", entao o portao deixou passar - e o portao esta certo; o
        # errado era casar no corpo.
        #
        # Mesma licao do banco de aulas, no mesmo dia: buscar no texto
        # explicativo faz qualquer palavra encostar em qualquer coisa.
        termos = "{titulo perguntas} : (" + " OR ".join(f"{p}*" for p in palavras) + ")"
        try:
            linha = self._conectar().execute(
                "SELECT s.resposta, f.titulo, f.perguntas,"
                " bm25(sobre_mim_fts, 0.0, 3.0, 10.0, 1.0) AS nota"
                " FROM sobre_mim_fts f JOIN sobre_mim s ON s.id = f.id"
                " WHERE sobre_mim_fts MATCH ? ORDER BY nota LIMIT 1",
                (termos,),
            ).fetchone()
        except sqlite3.OperationalError:
            logger.warning("busca de autoconhecimento rejeitada pelo FTS5: %r", termos)
            return None
        if linha is None or linha["nota"] > _TETO_BM25:
            return None
        # COBERTURA: uma palavra solta nao autoriza a resposta quando a frase
        # trouxe mais assunto. Achado em uso real (08/08): "voce consegue
        # fazer isso?" foi respondida com o fato da LUZ - "consegue" aparece
        # na pergunta indexada "o que voce consegue ligar" - e "gire para o
        # lado esquerdo", um COMANDO, virou a palestra sobre servos por causa
        # de "lado". Uma palavra generica encosta em qualquer coisa; duas ja
        # apontam um assunto. Quando a frase so tem uma palavra de conteudo
        # ("voce tem camera?"), ela basta.
        tokens_fato = re.sub(
            r"[^\w\s]", " ", _sem_acento(linha["titulo"] + " " + linha["perguntas"])
        ).split()
        # Palavra inteira com sufixo de plural/genero, nao qualquer prefixo:
        # "dia" nao e "diario", "chama" nao e "chamada" (2026-10-25, mesma
        # regra do saber_buscar no Pi - sem palavra de ativacao, toda fala
        # passa por aqui antes da IA).
        conjunto_fato = set(tokens_fato)
        cobertas = sum(
            1
            for p in palavras
            if any(p + sufixo in conjunto_fato for sufixo in _SUFIXOS_FLEXAO)
        )
        if cobertas < min(2, len(palavras)):
            return None
        return linha["resposta"]
