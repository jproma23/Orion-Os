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
        "varra a frente", "escaneie a frente",
    ),
    "andar_para_frente": (
        "anda para frente", "anda pra frente", "andar para frente",
        "vai para frente", "vai pra frente", "segue em frente",
        "va para frente", "anda para a frente",
        "ande para frente", "ande pra frente",
    ),
    "andar_para_tras": (
        "anda para tras", "anda pra tras", "andar para tras", "da re",
        "de re", "vai para tras", "vai pra tras", "volta para tras",
        "ande para tras", "ande pra tras", "recua", "recue",
    ),
    "calibrar_bussola": (
        "calibra a bussola", "calibrar a bussola", "calibre a bussola",
        "calibra sua bussola", "calibre sua bussola",
    ),
    "virar_esquerda": (
        "vira a esquerda", "vira para a esquerda", "vira pra esquerda",
        "virar a esquerda", "gira a esquerda", "gira para a esquerda",
        "vire a esquerda", "vire para a esquerda", "gire a esquerda",
        "gire para a esquerda",
    ),
    "virar_direita": (
        "vira a direita", "vira para a direita", "vira pra direita",
        "virar a direita", "gira a direita", "gira para a direita",
        "vire a direita", "vire para a direita", "gire a direita",
        "gire para a direita",
    ),
    "olhar_esquerda": (
        "olha para a esquerda", "olha pra esquerda", "olha para esquerda",
        "olhar para a esquerda", "olha a esquerda",
        "olhe para a esquerda", "olhe pra esquerda",
    ),
    "olhar_direita": (
        "olha para a direita", "olha pra direita", "olha para direita",
        "olhar para a direita", "olha a direita",
        "olhe para a direita", "olhe pra direita",
    ),
    "olhar_cima": (
        "olha para cima", "olha pra cima", "olhar para cima",
        "levanta a cabeca", "levante a cabeca",
        "olhe para cima", "olhe pra cima",
    ),
    "olhar_baixo": (
        "olha para baixo", "olha pra baixo", "olhar para baixo",
        "abaixa a cabeca", "abaixe a cabeca",
        "olhe para baixo", "olhe pra baixo",
    ),
    "olhar_frente": (
        "olha para frente", "olha pra frente", "olhar para frente",
        "endireita a cabeca", "cabeca no centro", "olha pro centro",
        "olhe para frente", "olhe pra frente",
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


# ---------------------------------------------------------------------------
# ALGUEM PEDIU ISTO? - o que decide se a acao vira FALA
# ---------------------------------------------------------------------------
#
# Dito pelo dono em 2026-08-09, depois de eu propor barrar a acao:
#
# > *"ele pode se mexer sem ninguem pedir, ele e autonomo; o q precisa e nao
# > ficar falando coisas q nao tem nada a ver"*
#
# Entao a acao NAO e barrada aqui - ela acontece do mesmo jeito. O que muda e
# a FALA: a frase pronta do catalogo ("Olhando pra frente.") so e dita quando
# a pessoa realmente pediu aquilo. Medido no chat de 08 e 09/08: em 26 de 123
# respostas o robo anunciou um movimento no meio de outro assunto - "Voce
# conhece a Biblia?" recebeu "Olhando pra frente."; "Me explica ai alguma
# coisa" recebeu "Varrendo a frente.". O corpo podia mexer; a narracao e que
# nao tinha o que fazer ali.
#
# POR QUE ISTO NAO E UMA LISTA NOVA: o vocabulario sai dos GATILHOS que ja
# existem acima. Acao nova ganha gatilho (a conferencia do import obriga) e
# ganha vocabulario junto - nao ha uma segunda tabela para envelhecer calada,
# que e o defeito que o dono mesmo nomeou neste projeto.

#: Palavras que aparecem em qualquer frase e nao dizem sobre o que ela fala.
#: Sem esta lista, "para" - que esta em quase todo gatilho E em quase toda
#: frase - autorizaria qualquer anuncio.
_VAZIAS = frozenset({
    "a", "o", "as", "os", "um", "uma", "de", "do", "da", "em", "no", "na",
    "para", "pra", "pro", "por", "ao", "aos", "e", "que", "se", "com", "sem",
    "meu", "minha", "seu", "sua", "me", "te", "voce", "eu", "ele", "ela",
    "isso", "isto", "aqui", "ai", "la", "mais", "menos", "muito", "bem",
    "faz", "fazer", "faca", "va", "vai", "vamos", "quero", "queria", "pode",
})


def _conteudo(frase: str) -> set[str]:
    """As palavras de uma frase que dizem alguma coisa."""
    return {p for p in frase.split() if p not in _VAZIAS and len(p) >= 3}


#: O vocabulario de cada acao, tirado dos proprios gatilhos dela.
_VOCABULARIO: dict[str, frozenset[str]] = {
    nome: frozenset().union(*(_conteudo(f) for f in frases))
    for nome, frases in {**GATILHOS, **GATILHOS_COM_ASSUNTO}.items()
}

#: Quantas letras iniciais bastam para duas formas do MESMO verbo casarem.
#: Quatro: "andando" e "andar" dividem "anda"; "olhe" e "olha" nao dividem
#: quatro letras, e por isso as duas formas estao escritas nos gatilhos. Menos
#: que isto ("olh", "vir") faria "virtude" casar com "virar".
_LETRAS_RADICAL = 4


def _mesma_palavra(dita: str, do_gatilho: str) -> bool:
    if dita == do_gatilho:
        return True
    if len(dita) < _LETRAS_RADICAL or len(do_gatilho) <= _LETRAS_RADICAL:
        return False
    return dita[:_LETRAS_RADICAL] == do_gatilho[:_LETRAS_RADICAL]


def combina_com_acao(nome_acao: str, texto: str) -> bool:
    """O texto fala do mesmo assunto que os gatilhos desta acao?

    Basta UMA palavra de conteudo em comum. Uma so, e nao duas como no
    autoconhecimento (08/08), porque aqui o custo de errar e invertido: pegar
    de menos deixa o robo mudo quando ele obedece, e "um robo que age em
    silencio parece um robo que travou".

    Acao sem gatilho (`deixar_recado`, `guardar_conhecimento`) devolve True: a
    fala delas nao e narracao de movimento, e a confirmacao do recado tem que
    sair sempre - prometer calado e o defeito que criou aquele modulo.
    """
    vocabulario = _VOCABULARIO.get(nome_acao)
    if vocabulario is None:
        return True
    ditas = _conteudo(_normalizar(texto))
    return any(
        _mesma_palavra(dita, do_gatilho)
        for dita in ditas
        for do_gatilho in vocabulario
    )


#: As formas de MANDAR GUARDAR. Nao entram no `GATILHOS` porque nao acionam
#: nada sozinhas - `guardar_conhecimento` precisa de titulo e conteudo, e
#: separar os dois dentro da frase e trabalho do modelo (ver
#: SEM_GATILHO_DE_PROPOSITO). Aqui elas so respondem a UMA pergunta: alguem
#: mandou guardar isto?
GATILHOS_DE_GUARDAR: tuple[str, ...] = (
    "guarda isso", "guarda essa", "guarde isso", "guardar isso",
    "anota", "anote", "anotar", "toma nota",
    "lembra disso", "lembre disso", "lembra que", "lembre que",
    "nao esquece", "nao esqueca", "grava isso", "grave isso",
    "aprende isso", "aprenda isso", "memoriza", "memorize",
    "registra isso", "registre isso",
)


def pediu_para_guardar(fala: str) -> bool:
    """Alguem MANDOU guardar? So entao `guardar_conhecimento` escreve.

    Em 09/08 o robo gravou tres notas que ninguem mandou gravar, todas de
    frase mal transcrita pelo Whisper: *"o usuario se chama Joao PALMO"*,
    *"Servidores de GOMEN"* (era DNS) e *"os computadores da rede sao
    chamados de OSPVE"*. Quem escreveu o marcador foi o modelo, sozinho, no
    meio de uma conversa comum.

    Mexer-se sem pedir e autonomia e o dono quer assim; **gravar sem pedir e
    outra coisa**: o movimento passa, o registro fica, e depois vira "verdade"
    para o portao do "Eu ja sei?". Aqui o custo de errar nao expira.

    Negacao anula, como no `casar`: *"nao anota isso"* nao pode gravar.
    """
    normalizado = _normalizar(fala)
    for frase in GATILHOS_DE_GUARDAR:
        achado = re.search(rf"\b{re.escape(frase)}\b", normalizado)
        if achado is not None and not _negado(normalizado, achado.start()):
            return True
    return False


def pediu_de_fato(nome_acao: str, fala: str) -> bool:
    """A PESSOA pediu esta acao? Decide se o robo anuncia o que vai fazer.

    Nao decide se ele age - ver o cabecalho deste bloco. O que importa e que o
    texto conferido e o da pessoa, nunca o do modelo: e o modelo que escreve o
    marcador por conta propria, e e justamente isso que precisa de conferencia.
    """
    return combina_com_acao(nome_acao, fala)
