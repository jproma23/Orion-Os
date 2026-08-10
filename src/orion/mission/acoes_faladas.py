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
#:
#: O segundo grupo e o ARGUMENTO: o resto da LINHA, que so algumas acoes usam.
#: Fica limitado a uma linha porque o que vem depois e a fala, e nao ha
#: delimitador de fim melhor que a quebra de linha num texto que o modelo
#: escreve livremente.
_RE_MARCADOR = re.compile(rf"\A\s*{re.escape(MARCADOR)}\s*([a-z_]+)([^\n]*)\n?")

#: Separa o destinatario do conteudo dentro do argumento de um recado.
#: Barra vertical porque ela nao aparece em fala humana - virgula e dois
#: pontos aparecem dentro da propria mensagem ("diga que sim, eu vou").
SEPARADOR_ARGUMENTO = "|"

#: Quem pode executar uma acao. Conjunto fechado, conferido no import: uma
#: entrega escrita errada ("cabeça", com cedilha) cairia em nenhum despachante
#: e a acao nao faria nada, sem erro nenhum - a falha silenciosa de sempre.
ENTREGAS = frozenset({"hardware", "cabeca", "recado", "conhecimento", "internet"})

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
    #: Quem executa: "hardware" (um comando de firmware), "cabeca" (os servos)
    #: ou "recado" (a memoria). Campo EXPLICITO e nao deduzido dos outros: a
    #: versao anterior inferia pela presenca de `comando`, e acrescentar o
    #: terceiro tipo transformaria essa deducao num "se o nome for tal", que e
    #: onde catalogos comecam a mentir.
    entrega: str = "hardware"
    #: Comando de firmware, quando `entrega == "hardware"`.
    comando: str = ""
    #: Posicao da cabeca, quando `entrega == "cabeca"`.
    pan: float | None = None
    tilt: float | None = None
    #: Se a acao precisa do texto que vem depois do nome, na mesma linha.
    precisa_argumento: bool = False
    #: O argumento tem DUAS partes separadas por barra (`Mara | recado`)?
    #: Campo explicito e nao deduzido: ate 2026-08-07 as duas acoes com
    #: argumento tinham a mesma forma, e a regra virou 'toda acao com
    #: argumento usa barra'. A busca tem uma parte so - o assunto - e
    #: deduzir a forma pelo nome e onde catalogos comecam a mentir.
    argumento_em_duas_partes: bool = True
    #: O que o robo diz ao fazer. So e usado quando ele nao tem nada melhor a
    #: dizer - a resposta do proprio modelo tem preferencia, porque ela sabe
    #: do contexto e esta nao sabe.
    resposta: str = "Feito."


_CATALOGO: tuple[AcaoFalada, ...] = (
    # ---- O MUNDO LA FORA (2026-08-07) -----------------------------------
    # Pedido do dono: "o robo vai usar a internet para aprender sobre o
    # mundo" - e a delimitacao dele na mesma conversa: "e nao eventos de
    # seus sensores". Isto serve para o que e conhecimento geral; o que
    # acontece NESTA CASA continua vindo dos sensores e do banco, e nunca
    # daqui.
    AcaoFalada(
        nome="buscar",
        descricao=(
            "consultar a Wikipedia sobre um assunto do mundo que voce nao "
            "sabe (uma tecnologia, um lugar, um conceito, uma pessoa "
            "publica). Use quando lhe perguntarem algo de conhecimento "
            "geral que voce nao tem certeza. NAO use para o que acontece "
            "nesta casa - quem viu quem, que horas, onde estao as coisas: "
            "isso voce so sabe pelos seus registros. Escreva o assunto logo "
            "depois do nome da acao, sem mais nada"
        ),
        entrega="internet",
        precisa_argumento=True,
        # Uma parte so: o assunto. Nao ha rotulo a separar.
        argumento_em_duas_partes=False,
        resposta="Nao encontrei nada sobre isso.",
    ),
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
        entrega="cabeca",
        pan=-PAN_LADO,
        tilt=0.0,
        resposta="Olhando pra esquerda.",
    ),
    AcaoFalada(
        nome="olhar_direita",
        descricao="virar a cabeca para a direita",
        entrega="cabeca",
        pan=PAN_LADO,
        tilt=0.0,
        resposta="Olhando pra direita.",
    ),
    AcaoFalada(
        nome="olhar_cima",
        descricao="levantar a cabeca, olhar para cima ou para o teto",
        entrega="cabeca",
        pan=0.0,
        tilt=TILT_CIMA,
        resposta="Olhando pra cima.",
    ),
    AcaoFalada(
        nome="olhar_baixo",
        descricao="abaixar a cabeca, olhar para baixo ou para o chao",
        entrega="cabeca",
        pan=0.0,
        tilt=TILT_BAIXO,
        resposta="Olhando pra baixo.",
    ),
    AcaoFalada(
        nome="olhar_frente",
        descricao="voltar a cabeca ao centro, olhar para a frente, endireitar a cabeca",
        entrega="cabeca",
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
    # ---- RECADO (2026-08-03) --------------------------------------------
    # Nasceu de uma falha real: o dono pediu "avise a Mara quando a vir que eu
    # quero falar com ela", o robo respondeu que sim, e quando a Mara chegou
    # ele a cumprimentou e nao deu o recado. A promessa era do MODELO; nao
    # havia nada em lugar nenhum que a guardasse.
    #
    # E a UNICA acao com argumento, e a razao e que ela e a unica que carrega
    # informacao que so existe na frase: a quem, e o que. "Olhar para a
    # esquerda" e sempre a mesma coisa; um recado nunca e.
    AcaoFalada(
        nome="deixar_recado",
        descricao=(
            "guardar um recado para alguem e entregar quando essa pessoa "
            "aparecer (avise fulano que..., diga a fulano que..., quando "
            "voce ver fulano fale que...). Escreva o argumento como "
            "`nome da pessoa | a mensagem`, e a mensagem na TERCEIRA pessoa, "
            "como voce vai fala-la depois"
        ),
        entrega="recado",
        precisa_argumento=True,
        resposta="Combinado, eu aviso.",
    ),
    # ---- APRENDER UM FATO DA CASA (2026-08-04) ---------------------------
    # Pedido do dono: "vamos inserir conhecimento pra ele".
    #
    # O vault (memoria de longo prazo em texto livre) existia desde 23/07 com
    # ZERO notas dentro: `nota_escrever` funcionava ponta a ponta e nunca
    # tinha sido chamado por ninguem. Esta acao e o caminho que faltava.
    #
    # Vai para o VAULT e nao para a tabela `conhecimento` de proposito: a
    # tabela guarda par chave/valor e e alimentada sozinha pelo ExtratorFatos
    # a partir das conversas. Aqui e o contrario - e a pessoa DIZENDO uma
    # coisa para ele guardar, em texto livre, do tamanho que for.
    AcaoFalada(
        nome="guardar_conhecimento",
        descricao=(
            "guardar para sempre uma informacao que te ensinaram sobre a casa, "
            "as pessoas ou o mundo (guarda isso, anota que..., lembra que..., "
            "quero que voce saiba que...). Escreva o argumento como "
            "`titulo curto | a informacao completa`. O titulo e como voce vai "
            "reencontrar isso depois - use as palavras que alguem usaria ao "
            "perguntar. NAO use para recado a uma pessoa, isso e deixar_recado"
        ),
        entrega="conhecimento",
        precisa_argumento=True,
        resposta="Guardei.",
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
        if acao.entrega not in ENTREGAS:
            raise RuntimeError(f"acao {acao.nome!r}: entrega {acao.entrega!r} nao existe")
        if acao.entrega == "hardware" and not acao.comando:
            raise RuntimeError(f"acao {acao.nome!r} entrega por hardware e nao tem comando")
        if acao.entrega == "cabeca":
            if acao.pan is None or acao.tilt is None:
                # Meia posicao nao existe: mandar so o pan deixaria o tilt
                # onde a ultima ordem tiver deixado, e "olha pra esquerda"
                # daria resultados diferentes conforme o passado.
                raise RuntimeError(f"acao {acao.nome!r} mexe a cabeca sem pan/tilt completo")
            if not PAN_MIN <= acao.pan <= PAN_MAX:
                raise RuntimeError(f"acao {acao.nome!r}: pan {acao.pan} fora dos limites")
            if not TILT_MIN <= acao.tilt <= TILT_MAX:
                raise RuntimeError(f"acao {acao.nome!r}: tilt {acao.tilt} fora dos limites")
        if acao.entrega != "hardware" and acao.comando:
            # Dois caminhos ate o hardware no mesmo instante, e nenhum jeito de
            # dizer qual chegou primeiro.
            raise RuntimeError(f"acao {acao.nome!r} nao e de hardware mas tem comando")


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
        "Exemplos:\n"
        f"{MARCADOR}olhar_esquerda\n"
        "Olhando pra esquerda.\n\n"
        "Mas SO anuncie o movimento quando ele foi pedido ou tem a ver com o\n"
        "assunto. Se voce resolveu olhar em volta sozinho no meio de outra\n"
        "conversa, mande a linha da acao e responda o que perguntaram - sem\n"
        "narrar o movimento:\n\n"
        f"{MARCADOR}varrer_a_frente\n"
        "A fotossintese e como a planta faz o proprio alimento.\n\n"
        f"{MARCADOR}deixar_recado Mara {SEPARADOR_ARGUMENTO} o Joao Paulo quer "
        "falar com voce\n"
        "Combinado, aviso a Mara assim que eu a vir.\n\n"
        "NUNCA prometa avisar alguem sem escrever a linha do recado. Sem ela\n"
        "voce nao esta guardando nada, e a pessoa vai contar com isso.\n\n"
        "A MESMA REGRA VALE PARA O CORPO. Sem a linha da acao, voce NAO se\n"
        "mexeu: nenhuma roda girou, nenhum servo virou. Entao nunca escreva\n"
        "que foi, que chegou, que olhou ou que voltou.\n\n"
        "E mesmo COM a linha, voce nao sabe o que aconteceu depois: a acao\n"
        "acabou de ser mandada e voce ja esta falando. Diga o que vai fazer,\n"
        "nunca o que encontrou. 'Vou dar uma olhada' e verdade; 'dei uma\n"
        "olhada e esta tudo em ordem' e invencao, e e sobre a casa da familia.\n"
        "O que voce ESTA vendo agora vem so do bloco de fatos acima.\n\n"
        "Se pedirem algo que nao esta na lista - ir ate um comodo pelo nome,\n"
        "procurar uma pessoa, pegar um objeto - diga que ainda nao consegue e\n"
        "ofereca o que da. Nao descreva fazendo."
    )


def extrair(texto: str) -> tuple[AcaoFalada | None, str, str]:
    """Separa a acao pedida do que deve ser FALADO.

    Devolve `(acao, argumento, texto_limpo)`. Sem marcador, ou com um nome que
    nao esta no catalogo, devolve `(None, "", texto)` - o texto segue inteiro e
    a frase vira conversa normal. Inventar acao a partir de nome desconhecido
    seria exatamente o que este catalogo existe para impedir.

    Acao que EXIGE argumento e veio sem ele tambem cai fora. Um `deixar_recado`
    vazio guardaria um recado sem destinatario e sem mensagem, que nunca seria
    entregue a ninguem - e o robo teria dito "combinado" assim mesmo. Prometer
    e nao ter como cumprir e pior que dizer que nao entendeu.
    """
    casado = _RE_MARCADOR.match(texto)
    if casado is None:
        return None, "", texto
    resto = texto[casado.end():].lstrip()
    argumento = casado.group(2).strip()
    acao = ACOES.get(casado.group(1))
    if acao is None:
        # Nome fora do catalogo: o marcador some do texto assim mesmo. Ele
        # nao pode ser FALADO, e deixa-lo passar seria transformar uma
        # alucinacao do modelo em ruido no alto-falante.
        return None, "", resto
    if acao.precisa_argumento and not argumento:
        return None, "", resto
    return acao, argumento, resto


def partir_argumento(argumento: str) -> tuple[str, str]:
    """`"Mara | quero falar com ela"` -> `("Mara", "quero falar com ela")`.

    Serve as duas acoes que carregam argumento, porque as duas tem a mesma
    forma: um rotulo curto e um texto. No recado o rotulo e a PESSOA; no
    conhecimento e o TITULO da nota.

    Devolve `("", "")` quando falta uma das duas metades. Adivinhar seria
    perigoso nos dois sentidos: sem separador, tratar tudo como rotulo
    guardaria um recado para uma pessoa chamada "mara quero falar com ela", e
    tratar tudo como texto guardaria um recado para ninguem.
    """
    if SEPARADOR_ARGUMENTO not in argumento:
        return "", ""
    nome, _, mensagem = argumento.partition(SEPARADOR_ARGUMENTO)
    nome, mensagem = nome.strip(), mensagem.strip()
    if not nome or not mensagem:
        return "", ""
    return nome, mensagem
