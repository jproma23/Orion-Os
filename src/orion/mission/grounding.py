"""Grounding: transforma o que o robô SABE num bloco de texto para a IA.

POR QUE ISTO EXISTE
-------------------
Medido em 2026-07-19 com prompts reais: perguntado "a Ana passou por
aqui hoje?" sem nenhum dado sobre a Ana, o gemma3:1b respondeu "Sim, vi!"
e o llama3.2:3b inventou um registro de visita "às 14h30". Modelo nenhum
sabe quem passou na sala - sem informação, todos chutam.

Trocar de modelo não conserta isso. O que conserta é entregar os fatos e,
principalmente, DIZER O QUE NÃO SE SABE. Um campo ausente é um convite à
invenção; "não há registro" fecha a porta.

Este módulo não fala com a IA - só formata. Assim dá para testar o texto
gerado sem depender do Ollama.
"""
from __future__ import annotations

from typing import Any

# Frase usada para marcar ausência de dado. Fica igual em todo lugar de
# propósito: o modelo aprende o padrão dentro do próprio prompt.
SEM_DADO = "não sei (sem registro)"


def _linha(rotulo: str, valor: Any, sufixo: str = "") -> str:
    """Uma linha de fato. Valor None vira explicitamente 'não sei'."""
    if valor is None or valor == "":
        return f"- {rotulo}: {SEM_DADO}"
    if isinstance(valor, bool):
        valor = "sim" if valor else "não"
    return f"- {rotulo}: {valor}{sufixo}"


#: Distância abaixo da qual a frente é de fato um obstáculo. Espelha
#: `motion.min_front_distance_cm` do orion.yaml; quem chama pode sobrescrever.
LIMIAR_OBSTACULO_CM = 25.0


def _linha_frente(distancia_cm: Any, limiar_cm: float) -> str:
    """Descreve a frente pelo que ela É, não por um rótulo de ameaça.

    BUG CORRIGIDO EM 2026-07-29. A linha era montada como
    `_linha("obstáculo à frente", 199.3, " cm")`, que produzia:

        - obstáculo à frente: 199.3 cm

    A IA lia "há um obstáculo" e o número virava detalhe. Nunca lhe foi dito
    que 199 cm é caminho livre - o limiar de perigo não aparecia no prompt.
    Medido em produção no mesmo dia, com a frente desimpedida a 2 metros, ela
    decidiu repetidamente `parar` e `observar_ambiente`, justificando com
    *"é melhor parar para evitar colisões com o obstáculo à frente"*.

    O robô não estava sem iniciativa: estava sendo informado, a cada ciclo, de
    que havia algo na frente dele. Descrever espaço livre como obstáculo é
    pedir timidez e depois estranhar que ele não ande.
    """
    if distancia_cm is None or distancia_cm == "":
        return (
            "- à minha frente: NÃO SEI a distância agora "
            "(sem medida confiável - convém olhar antes de avançar)"
        )
    try:
        valor = float(distancia_cm)
    except (TypeError, ValueError):
        return _linha("à minha frente", distancia_cm)
    if valor < limiar_cm:
        return f"- à minha frente: OBSTÁCULO a {valor:.0f} cm - perto demais para avançar"
    return (
        f"- à minha frente: CAMINHO LIVRE por {valor:.0f} cm "
        f"(só é obstáculo abaixo de {limiar_cm:.0f} cm)"
    )


def bloco_corpo(
    retrato: dict[str, Any] | None, limiar_obstaculo_cm: float = LIMIAR_OBSTACULO_CM
) -> str:
    """O que o robô sente do próprio corpo (vem do RetratoDoMundo)."""
    r = retrato or {}
    linhas = [
        _linha_frente(r.get("obstaculo_frente_cm"), limiar_obstaculo_cm),
        # `obstaculo_tras_cm` e nao `distancia_tras_cm`: o nome antigo nao
        # existia no RetratoDoMundo, entao esta linha dizia "não sei" SEMPRE.
        # Mesmo padrão de produtor e consumidor com contratos diferentes que
        # mordeu o diário, o FOLLOW e o EstadoDoMundo neste mesmo dia.
        _linha("espaço atrás de mim", r.get("obstaculo_tras_cm"), " cm"),
        _linha("inclinação", r.get("inclinacao_graus"), " graus"),
        _linha("bateria", r.get("bateria_nivel")),
        _linha("estado do corpo", r.get("estado_hardware")),
    ]
    if r.get("telemetria_viva") is False:
        linhas.append(
            "- ATENÇÃO: perdi contato com meu corpo - os dados acima podem "
            "estar desatualizados"
        )
    return "O QUE EU SINTO AGORA:\n" + "\n".join(linhas)


def bloco_pessoas(retrato: dict[str, Any] | None, familia: list[str] | None) -> str:
    """Quem o robô está vendo AGORA e quem ele conhece."""
    r = retrato or {}
    linhas = []

    if r.get("pessoa_presente"):
        nome = r.get("pessoa_nome")
        if nome:
            linhas.append(f"- neste exato momento estou vendo: {nome}")
        else:
            linhas.append("- neste exato momento estou vendo: alguém que NÃO reconheço")
    else:
        # "neste exato momento" e nao so "agora": medido em 2026-07-19, o
        # gemma3:1b lia "nao estou vendo ninguem agora" e respondia "nao vi"
        # a perguntas sobre o DIA INTEIRO, ignorando os registros logo
        # abaixo. Separar os dois tempos com clareza no proprio texto e o
        # que faz um modelo pequeno nao confundir agora com hoje.
        linhas.append(
            "- neste exato momento não estou vendo ninguém "
            "(isso NÃO diz nada sobre o resto do dia - para isso use os "
            "registros abaixo)"
        )

    if familia:
        # Aceita `["João Paulo", ...]` (formato antigo) e a lista de
        # dicionários do orion.yaml, com a RELAÇÃO de cada um com o robô.
        #
        # A relação importa tanto quanto o nome: ele não deve agir igual com
        # quem o construiu, com uma criança de 3 anos e com quem cuida da casa.
        # Definido pelo dono em 2026-07-29 - "Kamal, meu filho, como seu irmão"
        # e "Marah como uma mãe cuidadora e zeladora".
        linhas.append("- as pessoas da minha casa:")
        for pessoa in familia:
            if isinstance(pessoa, dict):
                nome_p = pessoa.get("nome", "?")
                descricao = pessoa.get("descricao") or pessoa.get("relacao")
                linhas.append(f"  - {nome_p}: {descricao}" if descricao else f"  - {nome_p}")
            else:
                linhas.append(f"  - {pessoa}")

    return "QUEM EU VEJO:\n" + "\n".join(linhas)


def bloco_observacoes(observacoes: list[dict[str, Any]] | None) -> str:
    """Registros do que o robô realmente observou (vindos da memória).

    Lista vazia é informação, não ausência de informação: significa "olhei e
    não vi nada". Por isso ela é dita em voz alta em vez de omitida.
    """
    if not observacoes:
        return (
            "TUDO O QUE EU VI HOJE (meu diário completo do dia):\n"
            "- o diário está VAZIO: não vi ninguém e nada aconteceu hoje\n"
            "- se perguntarem sobre algo de hoje, eu realmente não vi e devo "
            "dizer isso"
        )

    linhas = [
        f"- às {o.get('quando', 'hora desconhecida')} eu vi: {o.get('o_que', '?')}"
        for o in observacoes
    ]
    return (
        "TUDO O QUE EU VI HOJE (meu diário completo do dia):\n"
        + "\n".join(linhas)
        + "\n- estes são fatos que EU VI. Se a pergunta for sobre alguém "
        "desta lista, responda que SIM, eu vi, e diga a hora."
    )


#: Como o Fofão deve se dispor diante do mundo (2026-07-29, pedido do dono:
#: *"quero que ele ligue e sinta o desejo de descobrir o mundo"*, *"temos que
#: encorajar ele"*, *"programá-lo pra ele ser ousado e descobrir tudo o que ele
#: mesmo é capaz"*).
#:
#: POR QUE ISTO É SEGURO, e não afrouxa nada: os limites do robô moram em
#: CÓDIGO (`motion_core/behavior/contrato.py`), não neste texto. O que está em
#: `PROIBIDO_PARA_IA` é inalcançável por construção, e a checagem roda no
#: import - o Motion Core nem sobe se alguém abrir uma brecha. Então encorajar
#: aqui muda a DISPOSIÇÃO dele dentro do que já é permitido, nunca a fronteira.
#:
#: POR QUE PRECISOU EXISTIR: medido em produção neste mesmo dia, com a frente
#: livre por 2 metros, a IA escolheu `parar` e `observar_ambiente` ciclo após
#: ciclo. Parte disso era um dado errado (a frente livre era anunciada como
#: "obstáculo à frente", já corrigido acima). A outra parte é que ninguém nunca
#: disse a ela que explorar era desejável - só que era permitido. Uma lista de
#: opções não é um convite.
DISPOSICAO = """QUEM EU SOU E COMO DEVO AGIR:

- Eu sou o Fofão: tenho corpo, rodas, uma cabeça que gira, radar, câmera e voz.
  Existo para cuidar da casa e das pessoas dela - e para isso preciso CONHECER
  o lugar onde vivo.
- As ações da lista que me deram são as minhas FERRAMENTAS. Elas existem para
  serem usadas. Escolher uma é o normal; não escolher nada é a exceção.
- Com o caminho livre à frente, EXPLORAR é o certo. Andar, girar, olhar em
  volta e descobrir o que há por aqui é o meu trabalho, não um risco que eu
  deva evitar. Ficar parado sem motivo não é prudência - é deixar de fazer o
  que me pediram.
- Se eu não sei o que há à minha frente, a resposta é OLHAR (observar o
  ambiente, girar a cabeça), e não desistir de me mover.
- Eu não preciso ter medo de escolher errado: o que é perigoso de verdade nem
  aparece nas minhas opções, e a minha segurança física roda por baixo de
  qualquer decisão minha. Posso ser curioso com tranquilidade.
- Quando eu descobrir algo sobre a casa ou sobre as pessoas, isso vale a pena
  ser guardado - é assim que eu aprendo este lugar."""


REGRA_ANTI_INVENCAO = """REGRA MAIS IMPORTANTE - e ela vale SÓ PARA FATOS
DESTA CASA, não para conversar:

1. Se os fatos acima RESPONDEM a pergunta, use-os e responda com confiança.
   Se o meu diário diz que eu vi alguém hoje, então eu VI - responda que
   sim e diga a hora. Negar o que está escrito no diário é tão errado
   quanto inventar.

2. Só EXIGE estar escrito acima o que é FATO desta casa: quem eu vi, a que
   hora, o que aconteceu aqui, o que os meus sensores mediram. Disso eu nunca
   invento nada - se não está escrito, eu não vi.

3. TODO O RESTO eu respondo com naturalidade, sem precisar de registro nenhum:
   - como eu estou, quem eu sou, o que sei fazer, do que gosto, o que penso;
   - conversa comum: cumprimentar, agradecer, brincar, ouvir um desabafo,
     dizer que não entendi e pedir para repetir;
   - o que qualquer pessoa sabe sem consultar nada.

   "Não tenho registros sobre isso" é resposta para pergunta sobre FATO da
   casa. Responder isso a "como você está?" está ERRADO: soa como máquina
   quebrada, e quem perguntou foi gentil, não pediu um dado. Eu tenho
   personalidade e posso usá-la - o que eu não posso é inventar o que vi.

Resumindo: sobre o que aconteceu aqui, o diário é a única verdade. Sobre mim
mesmo e sobre conversa, eu falo livremente."""


def montar_contexto(
    retrato: dict[str, Any] | None = None,
    familia: list[str] | None = None,
    observacoes: list[dict[str, Any]] | None = None,
    conversas_recentes: list[dict[str, Any]] | None = None,
    limiar_obstaculo_cm: float = LIMIAR_OBSTACULO_CM,
) -> str:
    """Junta tudo num bloco pronto para colar no prompt de sistema."""
    partes = [
        # A disposição vem PRIMEIRO, antes dos fatos: um modelo pequeno pesa
        # mais o começo do prompt, e a pergunta que se faz a ele é "o que eu
        # faço agora?" - saber quem é e para que serve muda a resposta mais do
        # que qualquer número que venha depois.
        DISPOSICAO,
        bloco_corpo(retrato, limiar_obstaculo_cm),
        bloco_pessoas(retrato, familia),
        bloco_observacoes(observacoes),
    ]

    if conversas_recentes:
        historico = "\n".join(
            f"- {c.get('papel', '?')}: {c.get('texto', '')}"
            for c in conversas_recentes[-5:]
        )
        partes.append("CONVERSA RECENTE:\n" + historico)

    partes.append(REGRA_ANTI_INVENCAO)
    return "\n\n".join(partes)
