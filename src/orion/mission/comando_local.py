"""OBEDECER SEM IA - o caminho local ate o catalogo de acoes.

Achado do dono em 2026-08-07, com a cota diaria do Gemini esgotada:

> *"a cota diaria da ia acabou e eu descobri q ele nao faz nada sem ia ta
> errado"*

E a causa era estrutural: **quem escreve o marcador `#acao:` e o MODELO**
(ver `acoes_faladas.py`). Sem modelo, o marcador nunca aparece, e as 16
acoes do catalogo - luz, cabeca, ventilador, andar, buscar na internet -
ficam TODAS inalcancaveis. O robo tinha gasolina no tanque e a chave
trancada dentro do carro: ele so sabia dizer que nao conseguia pensar.

## Isto NAO e o intent classifier que foi recusado

Aquele foi recusado (ver a memoria da memoria de longo prazo, 02/08) por
tres motivos, e nenhum se aplica aqui:

1. *"casa por SUBSTRING, e 'parar' casa dentro de 'preparar'"* - aqui todo
   gatilho e casado com limite de palavra (`\\b`), e o teste
   `test_nao_casa_dentro_de_outra_palavra` existe por causa disso;
2. *"o modelo ja entende a intencao sozinho; quando a heuristica erra, ela
   ATRAPALHA"* - por isso este caminho e o degrau ABAIXO do modelo, e nao
   uma camada acima: so roda quando a IA NAO RESPONDEU. Com a IA no ar,
   nada aqui e consultado e o comportamento e exatamente o de antes;
3. *"um dos 10 tipos nao existe"* - aqui nao ha vocabulario proprio: os
   nomes vem do `ACOES`, e uma acao que nao exista nao tem como ser escrita.

## Por que a tabela mora aqui e nao no catalogo

Sao dois leitores diferentes: a `descricao` do catalogo e prosa para um
modelo escolher; o gatilho e frase exata para um casador. Junta-los deixaria
o catalogo ilegivel para os dois.

O que impede a tabela de envelhecer calada e a conferencia no import: acao
nova sem gatilho **derruba a subida**. O modo de falha que este projeto mais
repete e a peca que existe e ninguem chama - e uma acao sem gatilho seria
exatamente isso, so que descoberta no dia em que a IA cair.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from orion.mission.acoes_faladas import ACOES, AcaoFalada

logger = logging.getLogger("orion.mission.comando_local")

#: As frases que acionam cada acao sem modelo nenhum. Escritas como as
#: pessoas falam, sem acento (o texto e normalizado antes de casar).
#:
#: Varias formas por acao de proposito: quem esta com pressa fala "apaga a
#: luz", quem esta explicando fala "voce pode desligar a lanterna". Uma
#: forma so daria um robo que obedece a quem acerta a senha.
GATILHOS: dict[str, tuple[str, ...]] = {
    "acender_luz": (
        "acende a luz", "acender a luz", "liga a luz", "ligar a luz",
        "acende a lanterna", "liga a lanterna", "ligue a luz", "acenda a luz",
    ),
    "apagar_luz": (
        "apaga a luz", "apagar a luz", "desliga a luz", "desligar a luz",
        "apaga a lanterna", "desliga a lanterna", "apague a luz", "desligue a luz",
    ),
    "ligar_ventilador": (
        "liga o ventilador", "ligar o ventilador", "ligue o ventilador",
        "acende o ventilador",
    ),
    "desligar_ventilador": (
        "desliga o ventilador", "desligar o ventilador", "desligue o ventilador",
    ),
    "varrer_a_frente": (
        "varre a frente", "varrer a frente", "escaneia a frente",
        "escanear a frente", "varre na frente", "faz uma varredura",
    ),
    "andar_para_frente": (
        "anda para frente", "anda pra frente", "andar para frente",
        "vai para frente", "vai pra frente", "segue em frente",
        "va para frente", "anda para a frente",
    ),
    "virar_esquerda": (
        "vira a esquerda", "vira para a esquerda", "vira pra esquerda",
        "virar a esquerda", "gira a esquerda", "gira para a esquerda",
    ),
    "virar_direita": (
        "vira a direita", "vira para a direita", "vira pra direita",
        "virar a direita", "gira a direita", "gira para a direita",
    ),
    "olhar_esquerda": (
        "olha para a esquerda", "olha pra esquerda", "olha para esquerda",
        "olhar para a esquerda", "olha a esquerda",
    ),
    "olhar_direita": (
        "olha para a direita", "olha pra direita", "olha para direita",
        "olhar para a direita", "olha a direita",
    ),
    "olhar_cima": (
        "olha para cima", "olha pra cima", "olhar para cima",
        "levanta a cabeca", "levante a cabeca",
    ),
    "olhar_baixo": (
        "olha para baixo", "olha pra baixo", "olhar para baixo",
        "abaixa a cabeca", "abaixe a cabeca",
    ),
    "olhar_frente": (
        "olha para frente", "olha pra frente", "olhar para frente",
        "endireita a cabeca", "cabeca no centro", "olha pro centro",
    ),
}

#: Acoes com ARGUMENTO: o que vem depois do gatilho e o assunto. Separadas
#: das outras porque a forma e outra - ali a frase inteira e o comando, aqui
#: o comando e o comeco e o resto e conteudo.
GATILHOS_COM_ASSUNTO: dict[str, tuple[str, ...]] = {
    "buscar": (
        "busca sobre", "buscar sobre", "pesquisa sobre", "pesquisar sobre",
        "procura sobre", "procurar sobre", "busca na internet sobre",
        "pesquisa na internet sobre", "me fala sobre", "o que e",
        "o que sao", "quem e", "quem foi",
        # As formas de PERGUNTA que sobraram sem resposta em uso real
        # (07/08): "voce me explica porque o ceu e azul" caiu em "nao
        # consigo pensar" com a Wikipedia alcancavel ali do lado. Pode nao
        # achar - e ai ele diz que procurou e nao achou, que ja e uma
        # resposta melhor que a anterior.
        # "por que" ja cobre "por quê": o texto e normalizado sem acento
        # antes de casar, e escrever as duas formas aqui derrubaria a
        # conferencia de gatilho repetido, no import.
        "por que", "porque", "me explica", "me explique",
        "como funciona", "o que significa", "que que e",
    ),
}

#: NAO tem gatilho local, e a ausencia e deliberada: `deixar_recado` e
#: `guardar_conhecimento` precisam separar A QUEM de O QUE dentro da frase, e
#: errar isso nao produz silencio - produz uma PROMESSA FALSA ("combinado, eu
#: aviso") sobre um recado que ninguem vai receber. O modulo de recados nasceu
#: justamente de uma promessa dessas. Sem modelo, e melhor dizer que nao
#: consegue agora.
SEM_GATILHO_DE_PROPOSITO = frozenset({"deixar_recado", "guardar_conhecimento"})

#: Palavras que ANULAM o comando quando aparecem logo antes dele. "nao acende
#: a luz" nao pode acender a luz - e a frase existe, ninguem so pede o que
#: quer.
_NEGACOES = ("nao", "nunca", "jamais", "nem")

#: Quantas palavras antes do gatilho a negacao ainda conta. Duas: "nao acende"
#: (colada) e "por favor nao acende" (uma no meio).
_ALCANCE_NEGACAO = 2


@dataclass(frozen=True)
class Casamento:
    acao: AcaoFalada
    argumento: str = ""
    #: Que frase casou. Vai para o log: quando o robo obedecer errado, a
    #: primeira pergunta vai ser "o que foi que ele ouviu?".
    gatilho: str = ""


def _normalizar(texto: str) -> str:
    """Minusculas, sem acento e sem pontuacao, com espacos colapsados.

    A transcricao do Whisper varia em acento e pontuacao na mesma frase dita
    duas vezes; casar o texto cru daria um robo que obedece de vez em quando.
    """
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(re.sub(r"[^\w\s]", " ", sem_acento).split())


def _conferir_tabela() -> None:
    """Confere no import - ver o cabecalho.

    Duas direcoes, e as duas ja morderam este projeto: gatilho para acao que
    nao existe (erro de digitacao que nunca acionaria nada) e acao sem
    gatilho (inalcancavel no dia em que a IA cair).
    """
    todos = {**GATILHOS, **GATILHOS_COM_ASSUNTO}
    for nome in todos:
        if nome not in ACOES:
            raise RuntimeError(f"gatilho local para acao inexistente: {nome!r}")
    faltando = set(ACOES) - set(todos) - SEM_GATILHO_DE_PROPOSITO
    if faltando:
        raise RuntimeError(
            f"acoes sem gatilho local (ficariam inalcancaveis sem IA): {sorted(faltando)}"
        )
    vistos: dict[str, str] = {}
    for nome, frases in todos.items():
        for frase in frases:
            if frase != _normalizar(frase):
                raise RuntimeError(f"gatilho {frase!r} de {nome!r} nao esta normalizado")
            if frase in vistos:
                # A mesma frase em duas acoes daria um robo que faz uma coisa
                # ou outra conforme a ordem do dicionario.
                raise RuntimeError(f"gatilho {frase!r} repetido em {vistos[frase]!r} e {nome!r}")
            vistos[frase] = nome


_conferir_tabela()


def _negado(texto: str, inicio: int) -> bool:
    antes = texto[:inicio].split()
    return any(p in _NEGACOES for p in antes[-_ALCANCE_NEGACAO:])


def casar(texto: str) -> Casamento | None:
    """Acha a acao que a frase pede, sem modelo. None = nao e comando.

    O gatilho MAIS LONGO vence. "olha para a esquerda" contem "olha a
    esquerda"; sem essa regra, qual dos dois ganha dependeria da ordem do
    dicionario - e as duas apontam para a mesma acao hoje, mas nao ha nada
    que garanta isso amanha.
    """
    normalizado = _normalizar(texto)
    if not normalizado:
        return None

    melhor: Casamento | None = None
    for nome, frases in GATILHOS.items():
        for frase in frases:
            achado = re.search(rf"\b{re.escape(frase)}\b", normalizado)
            if achado is None or _negado(normalizado, achado.start()):
                continue
            if melhor is None or len(frase) > len(melhor.gatilho):
                melhor = Casamento(ACOES[nome], gatilho=frase)

    for nome, frases in GATILHOS_COM_ASSUNTO.items():
        for frase in frases:
            achado = re.search(rf"\b{re.escape(frase)}\b\s+(.+)", normalizado)
            if achado is None or _negado(normalizado, achado.start()):
                continue
            assunto = achado.group(1).strip()
            # Assunto de uma letra e ruido de transcricao, nao pergunta.
            if len(assunto) < 3:
                continue
            if melhor is None or len(frase) > len(melhor.gatilho):
                melhor = Casamento(ACOES[nome], argumento=assunto, gatilho=frase)

    if melhor is not None:
        logger.info(
            "comando local: %r -> %s (gatilho %r)", texto, melhor.acao.nome, melhor.gatilho
        )
    return melhor
