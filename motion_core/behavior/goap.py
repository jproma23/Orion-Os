"""GOAP - o Fofao planeja uma SEQUENCIA em vez de escolher um passo.

Pedido do dono em 2026-08-02: incorporar GOAP (Goal-Oriented Action Planning)
ao robo.

## O que muda, e o que NAO muda

Ate aqui a `IaEstrategica` (prioridade 40) perguntava ao modelo *"qual acao
voce escolhe?"* e recebia UM nome do catalogo. Isso funciona, mas tem um teto:
o robo nunca faz nada que exija dois passos encadeados. Se ele quer seguir uma
pessoa e nao esta vendo ninguem, `seguir_pessoa` simplesmente nao serve - e
nada no sistema sabe dizer *"entao olhe em volta primeiro"*.

Com GOAP a IA passa a escolher um **OBJETIVO** (um estado de mundo desejado) e
quem monta o caminho ate la e este planejador, buscando pelo catalogo de
acoes. O exemplo acima vira um plano de dois passos:

    quero: perto_da_pessoa
    tenho: pessoa_visivel = False
    plano: olhar_em_volta  ->  seguir_pessoa

**Isto SUBSTITUI a escolha de acao unica; nao e uma camada nova por cima.**
A distincao importa porque empilhar camadas e o defeito que este projeto mais
repete, apontado pelo proprio dono. O maestro continua arbitrando por
prioridade, a seguranca continua por baixo de tudo, e o contrato continua
sendo o unico lugar que define o que a IA alcanca. O que sai de cena e o passo
"o modelo nomeia uma acao"; o que entra e "o modelo nomeia um objetivo".

## A regra que mantem isto honesto

**Todo efeito declarado por uma acao tem que ser observavel no
`RetratoDoMundo`.** Se uma acao pudesse prometer um efeito que nada mede, o
planejador acreditaria ter chegado ao objetivo sem ninguem para desmentir - e
isso e a mesma familia do bug que este projeto ja teve tres vezes: publicar
num topico que ninguem escuta, sem erro nenhum aparecer. Aqui a rede e
`conferir_modelo()`, que roda no import e derruba o Motion Core se alguem
declarar um predicado que `estado_de` nao sabe produzir.

## Efeito e INTENCAO, nao promessa

`virar_esquerda` declara que deixa a frente livre. Ela nao garante isso - ela
TENTA. E assim que GOAP funciona em robotica: o plano e feito com os efeitos
pretendidos e a realidade e reconferida a cada passo. Quando o mundo discorda,
replaneja. Por isso `planejar()` e barato e deve ser chamado de novo a cada
ciclo, em vez de guardar um plano longo e confiar nele.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Iterable, Mapping

#: Limiar de "frente livre", em centimetros.
#:
#: Deliberadamente MAIOR que o limiar da seguranca reativa do firmware (25 cm
#: em safety_manager.h). Os dois numeros respondem perguntas diferentes: la e
#: "estou prestes a bater?", aqui e "vale a pena tentar andar?". Planejar um
#: avanco com 30 cm de folga daria um plano que a realidade cancela no passo
#: seguinte - plano que nasce condenado e ruido, nao decisao.
FRENTE_LIVRE_CM = 60.0

#: Acima disto o robo esta inclinado demais para planejar movimento.
#: Igual ao LIMITE_INCLINACAO_GRAUS do firmware, de proposito: dois numeros
#: diferentes para a mesma pergunta viram divergencia silenciosa.
INCLINACAO_LIMITE_GRAUS = 20.0

#: O vocabulario de estado. NADA fora desta lista pode aparecer numa
#: precondicao ou efeito - `conferir_modelo()` recusa no import.
#:
#: Cada um destes sai de um campo REAL do RetratoDoMundo (ver `estado_de`).
#: A tentacao aqui e inventar predicados uteis - "luz_acesa",
#: "bussola_calibrada" - que nenhum sensor reporta. Um predicado que ninguem
#: mede nunca fica falso, entao o planejador o considera resolvido para
#: sempre e o robo para de fazer aquilo. Falha silenciosa, do tipo caro.
PREDICADOS: frozenset[str] = frozenset(
    {
        "corpo_vivo",
        "frente_conhecida",
        "frente_livre",
        "pessoa_visivel",
        "pessoa_conhecida",
        "nivelado",
        "bateria_ok",
        "sem_impacto",
    }
)

#: Um estado do mundo: predicado -> verdadeiro/falso.
Estado = Mapping[str, bool]


def estado_de(retrato) -> dict[str, bool]:
    """Traduz o `RetratoDoMundo` no vocabulario do planejador.

    Recebe o retrato (duck-typed, para os testes nao precisarem montar um
    EventBus inteiro) e devolve so booleanos.

    **Desconhecido vira False, sempre.** `obstaculo_frente_cm is None`
    significa que o robo NAO SABE o que ha na frente - e nao saber tem que
    impedir um plano que dependa de caminho livre, nao liberar. E a mesma
    escolha que o firmware faz ao tratar leitura invalida como duvida.
    """
    frente = getattr(retrato, "obstaculo_frente_cm", None)
    inclinacao = getattr(retrato, "inclinacao_graus", None)
    nivel = getattr(retrato, "bateria_nivel", None)

    return {
        # Sem telemetria o corpo pode estar desligado, resetando ou com o
        # cabo solto. Nenhum plano que mexa no robo faz sentido nesse caso, e
        # e por isso que quase toda acao exige este predicado.
        "corpo_vivo": bool(getattr(retrato, "telemetria_viva", False)),
        "frente_conhecida": frente is not None,
        "frente_livre": frente is not None and frente > FRENTE_LIVRE_CM,
        "pessoa_visivel": bool(getattr(retrato, "pessoa_presente", False)),
        "pessoa_conhecida": bool(getattr(retrato, "pessoa_conhecida", False)),
        "nivelado": inclinacao is not None and inclinacao < INCLINACAO_LIMITE_GRAUS,
        # bateria_nivel vem do firmware como OK/AVISO/CRITICA, ou None quando
        # o divisor nao esta montado - que e a situacao real hoje. None aqui
        # NAO pode virar False: isso proibiria o robo de se mover ate alguem
        # soldar dois resistores. Desconhecido, neste unico caso, e tratado
        # como "sem motivo para impedir" - e a diferenca esta em quem paga o
        # erro: um obstaculo nao visto machuca agora, uma bateria nao medida
        # e um risco gradual que o dono ja aceitou ao deixar o divisor fora.
        "bateria_ok": nivel != "CRITICA",
        "sem_impacto": not bool(getattr(retrato, "impacto", False)),
    }


@dataclass(frozen=True)
class Objetivo:
    """O que a IA quer que passe a ser verdade."""

    nome: str
    descricao: str
    #: Predicados que precisam valer no fim. So estes sao conferidos - o
    #: resto do mundo pode estar como estiver.
    alvo: Mapping[str, bool]


def _satisfaz(estado: Estado, alvo: Mapping[str, bool]) -> bool:
    return all(estado.get(chave) == valor for chave, valor in alvo.items())


def _aplicar(estado: Estado, efeitos: Mapping[str, bool]) -> dict[str, bool]:
    novo = dict(estado)
    novo.update(efeitos)
    return novo


def _chave(estado: Estado) -> tuple:
    """Estado em forma hashavel e estavel, para o conjunto de visitados."""
    return tuple(sorted(estado.items()))


def _distancia(estado: Estado, alvo: Mapping[str, bool]) -> int:
    """Heuristica do A*: quantos predicados do alvo ainda estao errados.

    E admissivel (nunca superestima) porque cada acao muda pelo menos um
    predicado - entao nunca da para fechar N pendencias em menos de N passos.
    Isso e o que garante que o primeiro plano encontrado e o mais curto.
    """
    return sum(1 for chave, valor in alvo.items() if estado.get(chave) != valor)


def planejar(
    estado_inicial: Estado,
    objetivo: Objetivo,
    acoes: Iterable,
    limite_passos: int = 4,
) -> list | None:
    """Acha a sequencia MAIS CURTA de acoes que leva ao objetivo.

    Devolve a lista de acoes na ordem de execucao, `[]` se o objetivo ja
    esta satisfeito, ou `None` se nao ha caminho.

    `None` e uma resposta legitima e frequente: com o corpo mudo, ou com um
    objetivo que depende de coisa que nao existe hoje, nao ha plano. Quem
    chama trata isso como abstencao - a mesma saida que o contrato ja da para
    resposta fora do catalogo (regra 6). O robo nao chuta.

    `limite_passos` existe para o planejador nunca virar o gargalo do ciclo de
    decisao. Quatro passos e mais do que qualquer objetivo atual precisa (o
    mais longo hoje tem dois) e mantem a busca em alguns milhares de nos no
    pior caso. Um plano de dez passos, com um mundo que muda a cada segundo,
    estaria errado antes de terminar.
    """
    inicial = dict(estado_inicial)
    if _satisfaz(inicial, objetivo.alvo):
        return []

    acoes = list(acoes)
    # Fila de prioridade do A*: (custo estimado ate o fim, passos dados,
    # desempate, estado, plano). O contador de desempate existe porque dois
    # estados podem empatar no custo e dicionarios nao se comparam - sem ele
    # o heapq levanta TypeError em vez de escolher qualquer um.
    contador = 0
    fila: list = [(_distancia(inicial, objetivo.alvo), 0, contador, inicial, [])]
    visitados: dict[tuple, int] = {_chave(inicial): 0}

    while fila:
        _, passos, _, estado, plano = heapq.heappop(fila)
        if passos >= limite_passos:
            continue

        for acao in acoes:
            if not _satisfaz(estado, acao.precondicoes):
                continue
            # Uma acao que nao muda nada so faria o plano crescer sem chegar
            # mais perto - e criaria ciclos na busca.
            proximo = _aplicar(estado, acao.efeitos)
            if proximo == estado:
                continue

            chave = _chave(proximo)
            if chave in visitados and visitados[chave] <= passos + 1:
                continue
            visitados[chave] = passos + 1

            plano_novo = plano + [acao]
            if _satisfaz(proximo, objetivo.alvo):
                return plano_novo

            contador += 1
            heapq.heappush(
                fila,
                (
                    passos + 1 + _distancia(proximo, objetivo.alvo),
                    passos + 1,
                    contador,
                    proximo,
                    plano_novo,
                ),
            )

    return None


def conferir_modelo(acoes: Iterable, objetivos: Iterable[Objetivo]) -> None:
    """Recusa, no IMPORT, qualquer predicado que ninguem sabe medir.

    Mesma filosofia do `_conferir_contrato` do contrato.py: falhar no boot em
    vez de descobrir em campo. O erro que isto pega e especifico e traicoeiro
    - uma acao que promete um efeito fora do vocabulario nunca tem esse efeito
    conferido por ninguem, entao o planejador o trata como conquistado e para
    de tentar. Nada quebra, nada aparece no log, e o robo simplesmente deixa
    de fazer aquela coisa.
    """
    for acao in acoes:
        for onde, mapa in (("precondicoes", acao.precondicoes), ("efeitos", acao.efeitos)):
            desconhecidos = set(mapa) - PREDICADOS
            if desconhecidos:
                raise RuntimeError(
                    f"modelo GOAP invalido: a acao {acao.nome!r} usa em {onde} "
                    f"o(s) predicado(s) {sorted(desconhecidos)!r}, que nao "
                    f"existe(m) em PREDICADOS - nada saberia medir isso"
                )
    for objetivo in objetivos:
        desconhecidos = set(objetivo.alvo) - PREDICADOS
        if desconhecidos:
            raise RuntimeError(
                f"modelo GOAP invalido: o objetivo {objetivo.nome!r} pede "
                f"{sorted(desconhecidos)!r}, que nao existe(m) em PREDICADOS"
            )
