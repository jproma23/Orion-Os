"""Contrato de autoridade da IA sobre o Fofao (EDR-0026).

Pedido do usuario em 2026-07-28: *"gostaria de autoridade a IA para poder ter
o controle total do robo, seguindo regras inviolaveis de contrato"*.

As duas metades importam na mesma medida. **Controle total**: a IA escolhe
entre tudo que o robo sabe fazer com seguranca, incluindo mover as rodas -
antes ela so tinha "descansar" e "observar_ambiente", nenhum dos dois com
consequencia fisica. **Contrato inviolavel**: o limite dessa autoridade mora
AQUI, em codigo, e nao no prompt.

A diferenca nao e estilo. Prompt e texto: se convence, se confunde, e ignorado
quando o modelo alucina, e muda de comportamento quando alguem troca o modelo.
Codigo nao negocia. Se a IA responder "MOVE_DISTANCE -100", nada acontece -
nao porque o prompt pediu bom senso, mas porque nao existe caminho daqui ate
la.

## As regras invioláveis

1. **A IA nunca emite comando cru.** Ela escolhe um NOME deste catalogo. O que
   aquele nome faz esta escrito aqui, nao no que ela respondeu.
2. **Movimento autonomo passa pela navegacao**, nunca direto ao Arduino - e o
   `NavigationCore` que checa obstaculo antes de avancar (camada tatica, Cap
   12 s.6) e que faz retentativa por segmento. Mandar direto ao Mega pula essa
   camada inteira.
3. **A seguranca reativa do Mega nunca e consultada.** Ela veta por cima de
   qualquer decisao daqui, sem pedir licenca (Cap 18, regra 2 do EDR-0020).
4. **Obstaculo (prio 100) e operador (prio 95) sempre ganham.** A IA vive em
   40 e 35; nao existe decisao dela que suba nessa escada.
5. **O que esta em `PROIBIDO_PARA_IA` e inalcancavel**, e isso e verificado no
   import - nao em revisao de codigo, nem na boa vontade de quem editar isto
   depois.
6. **Fora do catalogo = abstencao.** Resposta desconhecida nunca vira chute:
   vira "sem opiniao agora", e a escada de prioridades resolve.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Resposta que significa "nada relevante agora".
#:
#: NAO e uma acao, e por isso nao esta no catalogo: o usuario foi explicito
#: que "descansar" nao deve existir no diretorio do maestro - um robo cuidador
#: nao tem "nao fazer nada" entre as coisas que entrega.
#:
#: A abstencao continua existindo como MECANISMO, so que sem virar um verbo:
#: cai em None, ninguem assume o controle nesta camada, e `Ronda` (20) ou
#: `Repouso` (10) ficam com a vez pela escada normal. Sem isso a IA seria
#: obrigada a mandar o robo fazer alguma coisa a cada ciclo, para sempre.
ABSTENCAO = "nada"


@dataclass(frozen=True)
class Acao:
    """Uma entrada do catalogo: o que a IA pode escolher e o que isso faz."""

    nome: str
    descricao: str
    topico: str
    dados: dict = field(default_factory=dict)
    move_rodas: bool = False


#: Comandos de firmware que a IA NUNCA alcanca, por mais que decida.
#:
#: Nao e desconfianca do modelo - e que estes quatro grupos tem consequencia
#: que o robo nao consegue desfazer sozinho:
#:
#: - `MOVE_DISTANCE` e o comando da RE (distancia negativa). A re nao tem
#:   seguranca reativa: o SafetyManager so olha o sensor frontal (Cap 18).
#:   Andar para tras sozinho e andar cego.
#: - `MOVE_CONTINUOUS` so para quando alguem manda parar. Depender de a IA
#:   lembrar de mandar STOP e depender de a proxima chamada de API dar certo.
#: - `CALIBRATE_*` e `SET_IMPACT_THRESHOLD` mexem na propria seguranca do
#:   robo. Quem e vigiado nao ajusta o vigia.
#: - `DOCK` existe no firmware e nunca foi usado por ninguem, nem a mao. Nao
#:   se estreia comando de acoplamento por decisao autonoma.
PROIBIDO_PARA_IA = frozenset(
    {
        "MOVE_DISTANCE",
        "MOVE_CONTINUOUS",
        "CALIBRATE_IMU",
        "CALIBRATE_COMPASS",
        "SET_IMPACT_THRESHOLD",
        "DOCK",
    }
)

#: O catalogo. Tudo aqui se apoia em capacidade que JA existe e ja foi usada -
#: nenhuma entrada depende de codigo por escrever.
_CATALOGO: tuple[Acao, ...] = (
    Acao(
        nome="observar_ambiente",
        descricao="varrer a frente com o radar, sem sair do lugar",
        topico="navigation.comando",
        dados={"acao": "SCAN_FRONT"},
    ),
    Acao(
        nome="explorar",
        descricao="andar um trecho curto a frente, se estiver livre",
        topico="navigation.comando",
        dados={"acao": "EXPLORE"},
        move_rodas=True,
    ),
    Acao(
        nome="avancar",
        descricao="andar para a frente",
        topico="navigation.comando",
        dados={"acao": "MANUAL", "comando": "MOVE_FORWARD"},
        move_rodas=True,
    ),
    Acao(
        nome="virar_esquerda",
        descricao="girar no lugar para a esquerda",
        topico="navigation.comando",
        dados={"acao": "MANUAL", "comando": "TURN_LEFT"},
        move_rodas=True,
    ),
    Acao(
        nome="virar_direita",
        descricao="girar no lugar para a direita",
        topico="navigation.comando",
        dados={"acao": "MANUAL", "comando": "TURN_RIGHT"},
        move_rodas=True,
    ),
    Acao(
        nome="seguir_pessoa",
        descricao="acompanhar com as rodas quem ele reconhece",
        topico="navigation.comando",
        dados={"acao": "FOLLOW"},
        move_rodas=True,
    ),
    Acao(
        nome="parar",
        descricao="ficar imovel, com os sensores ligados",
        topico="navigation.comando",
        dados={"acao": "HOLD"},
    ),
    Acao(
        nome="acender_luz",
        descricao="acender a luz do robo",
        topico="navigation.comando",
        dados={"acao": "MANUAL", "comando": "LIGHT_ON"},
    ),
    Acao(
        nome="apagar_luz",
        descricao="apagar a luz do robo",
        topico="navigation.comando",
        dados={"acao": "MANUAL", "comando": "LIGHT_OFF"},
    ),
    Acao(
        nome="ligar_ventilador",
        descricao="ligar o ventilador",
        topico="navigation.comando",
        dados={"acao": "MANUAL", "comando": "FAN_ON"},
    ),
    Acao(
        nome="desligar_ventilador",
        descricao="desligar o ventilador",
        topico="navigation.comando",
        dados={"acao": "MANUAL", "comando": "FAN_OFF"},
    ),
    Acao(
        nome="fotografar",
        descricao="registrar uma foto do que esta vendo",
        topico="sentinela.capturar_foto",
        dados={"motivo": "decisao_da_ia"},
    ),
)

ACOES: dict[str, Acao] = {acao.nome: acao for acao in _CATALOGO}

#: Nomes que vao no prompt, para a IA escolher.
NOMES: tuple[str, ...] = tuple(ACOES)


def _conferir_contrato() -> None:
    """Regra 5: o proibido e inalcancavel, e isso e verificado no IMPORT.

    Se alguem acrescentar amanha uma acao que caia num comando proibido, o
    Motion Core nao sobe - em vez de subir e so descobrirmos em campo, com o
    robo andando de re no escuro. Falhar no boot e o comportamento desejado.
    """
    for acao in _CATALOGO:
        comando = acao.dados.get("comando")
        if comando in PROIBIDO_PARA_IA:
            raise RuntimeError(
                f"contrato violado: a acao {acao.nome!r} usa {comando!r}, "
                f"que esta em PROIBIDO_PARA_IA"
            )
        if acao.move_rodas and acao.topico != "navigation.comando":
            raise RuntimeError(
                f"contrato violado: a acao {acao.nome!r} move as rodas mas nao "
                f"passa pela navegacao (topico {acao.topico!r}) - regra 2"
            )


_conferir_contrato()


def resolver(nome: str | None) -> Acao | None:
    """Traduz a escolha da IA numa acao do catalogo.

    Devolve None para abstencao, resposta vazia ou qualquer coisa fora do
    contrato (regra 6). Quem chama trata None como "sem opiniao agora".
    """
    if not nome:
        return None
    limpo = nome.strip().lower()
    if limpo == ABSTENCAO:
        return None
    return ACOES.get(limpo)
