"""O que o Fofao sabe FAZER quando alguem pede falando.

Pedido do dono em 2026-08-03: *"o robo tem que saber mexer a cabeca quando eu
pedir"*, *"ele tem que saber todos os comandos"*.

## O que estava errado

O caminho falado reconhecia SETE coisas, por expressao regular
(`_PADROES_COMANDO` no mission_planner): luz, parar, varrer, frente, esquerda,
direita. Nada mais. O catalogo do robo tem 16 acoes e a CABECA nao estava em
nenhuma das duas listas - nao havia como pedir "olha pra esquerda" por voz,
por mais claro que fosse o pedido.

E a deteccao por substring e o defeito que este projeto ja tomou: esta escrito
no `motion_core/behavior/contrato.py` que *"o caminho antigo casava por
SUBSTRING - e 'parar' casa dentro de 'preparar'"*. Cada comando novo por regex
seria mais uma armadilha dessas, e "olha pra cima" tem variacao demais
("levanta a cabeca", "olha pro teto", "poe a cabeca pra cima") para caber em
padrao fixo.

## O que passa a valer

Quem entende a frase e o modelo - que ja esta na conversa de qualquer jeito, e
que entende "levanta a cabeca" sem ninguem lhe ensinar. O que ele NAO faz e
escolher o que acontece: ele devolve um NOME deste catalogo, e o que aquele
nome faz esta escrito AQUI, em codigo.

E a regra 1 do contrato de autoridade, aplicada ao caminho falado:

    A IA nunca emite comando cru. Ela escolhe um NOME deste catalogo. O que
    aquele nome faz esta escrito aqui, nao no que ela respondeu.

Nome fora do catalogo nao vira chute: vira nada, e a frase segue como conversa
normal. Um modelo que alucina `olhar_para_o_infinito` nao encontra caminho
ate servo nenhum.

## Por que o marcador vai no COMECO da resposta

O robo fala frase a frase enquanto o modelo ainda escreve (0,66 s ate o
primeiro som, contra 3,50 s - custou uma sessao inteira conquistar isso). Se a
acao viesse no FIM, o servo so se mexeria depois de a fala acabar: a pessoa
ouviria "olhando pra esquerda" com a cabeca parada, e so entao ela viraria.

No comeco, a acao dispara junto com a primeira frase. E o marcador e retirado
do texto antes de ele virar audio - ninguem quer ouvir "sustenido acao dois
pontos olhar esquerda".

## O que NAO esta aqui, de proposito

**Parar.** Continua sendo regex no mission_planner, e continua sendo a unica.
Nao e sobra do sistema antigo: e a unica ordem que nao pode depender de a
internet estar de pe. Todo o resto deste catalogo passa pelo modelo remoto;
"para!" nao passa.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Como o modelo pede uma acao: primeira linha da resposta, e so ela.
MARCADOR = "#acao:"

#: Casa o marcador no comeco do texto, com ou sem espaco depois dos dois
#: pontos, e come a quebra de linha seguinte para nao sobrar linha vazia na
#: fala. `\A` e nao `^` de proposito: sem MULTILINE, um marcador no meio do
#: texto e texto, nao comando.
_RE_MARCADOR = re.compile(rf"\A\s*{re.escape(MARCADOR)}\s*([a-z_]+)\s*\n?")

#: Limites dos servos da cabeca. Os MESMOS numeros do webui/server.py e do
#: joystick - ver a nota la sobre numero repetido: melhor repetido e preso por
#: teste que um servo forcado contra o batente, que ja derrubou o ESP32
#: inteiro uma vez (2026-08-03).
PAN_MIN, PAN_MAX = -80.0, 80.0
TILT_MIN, TILT_MAX = -30.0, 45.0

#: Quanto o robo vira quando alguem diz so "olha pra esquerda", sem medida.
#:
#: NAO e o limite do servo, e sim uma folga antes dele. Encostar no batente faz
#: o servo empurrar contra o fim de curso e puxar corrente ate travar - foi
#: assim que um servo em estol derrubou o ESP32 inteiro. "Bem para a esquerda"
#: e 60 graus; os 80 ficam de reserva para quem sabe o que esta pedindo.
PAN_LADO = 60.0
TILT_CIMA = 35.0
TILT_BAIXO = -25.0


@dataclass(frozen=True)
class AcaoFalada:
    """Uma coisa que da para pedir falando.

    `descricao` NAO e documentacao: e o texto que o modelo le para escolher.
    Vale escrever como quem explica a uma pessoa, e nao como quem nomeia uma
    funcao.
    """

    nome: str
    descricao: str
    #: Comando de firmware, quando a acao se resolve num comando so.
    comando: str = ""
    #: Posicao da cabeca, quando a acao mexe a cabeca. Ausente = nao mexe.
    pan: float | None = None
    tilt: float | None = None
    #: O que o robo diz ao fazer. So e usado quando ele nao tem nada melhor a
    #: dizer - a resposta do proprio modelo tem preferencia, porque ela sabe
    #: do contexto e esta nao sabe.
    resposta: str = "Feito."


_CATALOGO: tuple[AcaoFalada, ...] = (
    # ---- A CABECA (2026-08-03) ------------------------------------------
    # Nenhuma destas existia. Era o buraco que o dono apontou: "ele tem que
    # saber mexer a cabeca quando eu pedir".
    #
    # SINAL: pan positivo e o mesmo lado para onde o joystick da webui manda
    # quando se arrasta o punho para a direita (camera_cabeca.js), e tilt
    # positivo e para cima - a mesma convencao que a visao ja usa para
    # rastrear rosto. Uma convencao so no robo inteiro; se estiver espelhada,
    # esta espelhada para todo mundo e o conserto e aqui.
    AcaoFalada(
        nome="olhar_esquerda",
        descricao="virar a cabeca para a esquerda (olha pra esquerda, olha pra la)",
        pan=-PAN_LADO,
        tilt=0.0,
        resposta="Olhando pra esquerda.",
    ),
    AcaoFalada(
        nome="olhar_direita",
        descricao="virar a cabeca para a direita",
        pan=PAN_LADO,
        tilt=0.0,
        resposta="Olhando pra direita.",
    ),
    AcaoFalada(
        nome="olhar_cima",
        descricao="levantar a cabeca, olhar para cima ou para o teto",
        pan=0.0,
        tilt=TILT_CIMA,
        resposta="Olhando pra cima.",
    ),
    AcaoFalada(
        nome="olhar_baixo",
        descricao="abaixar a cabeca, olhar para baixo ou para o chao",
        pan=0.0,
        tilt=TILT_BAIXO,
        resposta="Olhando pra baixo.",
    ),
    AcaoFalada(
        nome="olhar_frente",
        descricao="voltar a cabeca ao centro, olhar para a frente, endireitar a cabeca",
        pan=0.0,
        tilt=0.0,
        resposta="Olhando pra frente.",
    ),
    # ---- O que ja existia por regex, agora escolhido pelo modelo ---------
    AcaoFalada(
        nome="acender_luz",
        descricao="acender a luz ou a lanterna do robo",
        comando="LIGHT_ON",
        resposta="Lanterna ligada.",
    ),
    AcaoFalada(
        nome="apagar_luz",
        descricao="apagar a luz ou a lanterna do robo",
        comando="LIGHT_OFF",
        resposta="Lanterna desligada.",
    ),
    AcaoFalada(
        nome="varrer_a_frente",
        descricao="varrer a frente com o sensor de distancia, escanear o que tem na frente",
        comando="SCAN_FRONT",
        resposta="Varrendo a frente.",
    ),
    AcaoFalada(
        nome="andar_para_frente",
        descricao="andar para a frente, seguir em frente",
        comando="MOVE_FORWARD",
        resposta="Indo para frente.",
    ),
    AcaoFalada(
        nome="virar_esquerda",
        descricao="girar o corpo inteiro no lugar para a esquerda (o robo vira, nao so a cabeca)",
        comando="TURN_LEFT",
        resposta="Virando a esquerda.",
    ),
    AcaoFalada(
        nome="virar_direita",
        descricao="girar o corpo inteiro no lugar para a direita (o robo vira, nao so a cabeca)",
        comando="TURN_RIGHT",
        resposta="Virando a direita.",
    ),
    # ---- O que o catalogo do robo tinha e a voz nao alcancava ------------
    AcaoFalada(
        nome="ligar_ventilador",
        descricao="ligar o ventilador",
        comando="FAN_ON",
        resposta="Ventilador ligado.",
    ),
    AcaoFalada(
        nome="desligar_ventilador",
        descricao="desligar o ventilador",
        comando="FAN_OFF",
        resposta="Ventilador desligado.",
    ),
)

ACOES: dict[str, AcaoFalada] = {acao.nome: acao for acao in _CATALOGO}


def _conferir_catalogo() -> None:
    """O catalogo se confere no IMPORT, e nao em revisao de codigo.

    Mesma escolha do `contrato.py`: se alguem acrescentar amanha uma acao de
    cabeca fora dos limites do servo, o Mission Core nao sobe - em vez de subir
    e o servo descobrir o batente sozinho, que e o defeito que ja derrubou o
    ESP32 uma vez.
    """
    for acao in _CATALOGO:
        mexe_cabeca = acao.pan is not None or acao.tilt is not None
        if not mexe_cabeca and not acao.comando:
            raise RuntimeError(
                f"acao {acao.nome!r} nao faz nada: sem comando e sem cabeca"
            )
        if mexe_cabeca and acao.comando:
            # As duas coisas na mesma acao dariam dois caminhos ate o hardware
            # no mesmo instante, e nenhum jeito de dizer qual chegou primeiro.
            raise RuntimeError(
                f"acao {acao.nome!r} mexe a cabeca E manda comando - escolha um"
            )
        if acao.pan is not None and not PAN_MIN <= acao.pan <= PAN_MAX:
            raise RuntimeError(f"acao {acao.nome!r}: pan {acao.pan} fora dos limites")
        if acao.tilt is not None and not TILT_MIN <= acao.tilt <= TILT_MAX:
            raise RuntimeError(f"acao {acao.nome!r}: tilt {acao.tilt} fora dos limites")


_conferir_catalogo()


def bloco_para_prompt() -> str:
    """O catalogo como o modelo o le, gerado DAQUI e nunca copiado.

    Uma fonte so: acrescentar uma acao no catalogo ja a coloca no prompt. A
    alternativa - manter a lista tambem no `prompt_sistema.txt` - criaria duas
    listas que envelhecem em ritmos diferentes, e o defeito que o dono mesmo
    nomeou neste projeto e justamente empilhar camadas em vez de corrigir a
    fonte.
    """
    linhas = [f"- {acao.nome}: {acao.descricao}" for acao in _CATALOGO]
    return (
        "VOCE PODE AGIR NO MUNDO, nao so responder.\n"
        "Quando a pessoa PEDIR uma destas coisas, comece a resposta com uma\n"
        f"linha `{MARCADOR}nome_da_acao` e depois fale normalmente, curto.\n"
        "Se ninguem pediu nada disso, nao escreva essa linha - a maior parte\n"
        "da conversa nao e pedido.\n\n"
        "O que voce sabe fazer:\n" + "\n".join(linhas) + "\n\n"
        "Exemplo:\n"
        f"{MARCADOR}olhar_esquerda\n"
        "Olhando pra esquerda."
    )


def extrair(texto: str) -> tuple[AcaoFalada | None, str]:
    """Separa a acao pedida do que deve ser FALADO.

    Devolve `(acao, texto_limpo)`. Sem marcador, ou com um nome que nao esta no
    catalogo, devolve `(None, texto)` - o texto segue inteiro e a frase vira
    conversa normal. Inventar acao a partir de nome desconhecido seria
    exatamente o que este catalogo existe para impedir.
    """
    casado = _RE_MARCADOR.match(texto)
    if casado is None:
        return None, texto
    resto = texto[casado.end():].lstrip()
    acao = ACOES.get(casado.group(1))
    if acao is None:
        # Nome fora do catalogo: o marcador some do texto assim mesmo. Ele
        # nao pode ser FALADO, e deixa-lo passar seria transformar uma
        # alucinacao do modelo em ruido no alto-falante.
        return None, resto
    return acao, resto
