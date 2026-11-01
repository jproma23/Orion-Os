"""Contrato de autoridade da IA sobre o Jarvis (EDR-0026).

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
    #: ACAO COM NUMERO (2026-11-01). Nome da chave de `dados` que recebe o
    #: `valor` que a IA devolve (mm ou graus), multiplicado por `escala`, e o
    #: teto aceito. None = acao sem numero (as antigas).
    parametro: str | None = None
    escala: float = 1.0
    maximo: float = 0.0
    #: Nome do COMPORTAMENTO do maestro que executa esta acao, quando ela nao
    #: se resolve num unico evento.
    #:
    #: Existe por causa de um impasse de arbitragem real (2026-07-30): a
    #: `IaEstrategica` roda em prioridade 40 e, ao executar, SEGURA o controle
    #: num laco. Um comportamento de prioridade menor - a `Ronda` vive em 20 -
    #: nunca chegaria a assumir, mesmo tendo sido a propria IA a pedi-lo. Sem
    #: este campo, por o nome no catalogo trocaria uma trava por outra.
    #:
    #: Quem le isto e `IaEstrategica.executar`: acao delegada e publicada e o
    #: controle e SOLTO na hora, para o maestro poder entregar a vez a quem de
    #: fato executa.
    delega_para: str = ""


#: Comandos de firmware que a IA NUNCA alcanca, por mais que decida.
#:
#: A lista encolheu em 2026-07-28, por decisao do usuario. Cada saida veio com
#: a rede correspondente montada, nao no grito:
#:
#: - `MOVE_CONTINUOUS` (manobrar) e auto-limitado pelo PROPRIO firmware:
#:   `TIMEOUT_COMANDO_MS = 5000` em safety_manager.h para os motores sozinho
#:   se nenhum comando novo chegar em 5s. Minha objecao original - "so para
#:   se alguem lembrar de mandar STOP" - estava errada: o Mega lembra.
#: - `CALIBRATE_COMPASS` e o robo refazer a referencia da bussola, que e
#:   auto-ajuste - exatamente o que foi pedido. O `CALIBRATE_IMU` saiu junto
#:   com a MPU6050 (descartada em 2026-08-18).
#:
#: `MOVE_DISTANCE` (a RE) continua fora, mas por decisao do usuario e nao por
#: risco: *"na verdade ele nao precisa dar re, so basta girar no seu proprio
#: eixo"*. Girar no eixo resolve o mesmo problema sem andar para tras - e a re
#: e o unico movimento sem seguranca reativa nenhuma (o SafetyManager so olha
#: o radar frontal, Cap 18), entao evita-la de graca e o melhor negocio que
#: aparece aqui.
#:
#: O que resta nao e teimosia, e outra categoria: `DOCK` existe no firmware e
#: nunca foi usado por ninguem, nem a mao. Nao se estreia comando de
#: acoplamento por decisao autonoma: a primeira vez tem que ter alguem
#: olhando.
PROIBIDO_PARA_IA = frozenset(
    {
        "MOVE_DISTANCE",
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
    #: A `Ronda` existia registrada no maestro desde 19/07 e NUNCA rodou uma
    #: vez - 0 ocorrencias no log inteiro. Nao era falta de vontade da IA:
    #: nao havia caminho. Duas travas independentes, achadas em 2026-07-30:
    #:
    #:   1. `ronda` nao estava neste catalogo, e `resolver()` devolve None
    #:      para tudo que nao esta aqui - a IA nao PODIA escolhe-la.
    #:   2. O unico codigo que chamava `Ronda.pedir()` era
    #:      `behavior_core._consultar_ia`, que exige a `ponte_conselho` - e
    #:      ela e None, porque `conselho_ia.habilitado: false` no orion.yaml.
    #:
    #: O nome e o do dono ("olhar em volta"), nao o interno: o catalogo e o
    #: vocabulario que a IA le, e vale ele falar a lingua de quem pediu.
    #: Nao move rodas - so a cabeca e o radar, o que a torna reversivel.
    Acao(
        nome="olhar_em_volta",
        descricao="virar a cabeca de um lado ao outro e varrer com o radar",
        topico="behavior.ronda",
        dados={},
        delega_para="ronda",
    ),
    Acao(
        nome="explorar",
        # 2026-11-01: o EXPLORE agora desvia sozinho pelo LiDAR (desvio_local).
        descricao="explorar a casa: o corpo anda e desvia sozinho dos obstaculos",
        topico="navigation.comando",
        dados={"acao": "EXPLORE"},
        move_rodas=True,
    ),
    # --- ACOES COM NUMERO (2026-11-01) --------------------------------------
    # O dono: "ela precisa saber quando desviar e o quanto desvia em mm". O
    # retrato ja da as 8 direcoes e o desvio em mm; estas sao as acoes que
    # deixam a IA MANDAR esse numero. Verbos de NAVEGACAO (GOTO, GIRAR,
    # DESVIAR), e nao MANUAL: MANUAL acorda o ControleManual achando que um
    # humano pegou o joystick (ver NavigationCore.girar).
    Acao(
        nome="andar_mm",
        descricao="andar para a frente VALOR milimetros",
        topico="navigation.comando",
        dados={"acao": "GOTO", "graus": 0.0},
        move_rodas=True,
        parametro="distancia_cm",
        escala=0.1,
        maximo=1500.0,
    ),
    Acao(
        nome="girar_esquerda_graus",
        descricao="girar no lugar VALOR graus para a esquerda (a bussola confere)",
        topico="navigation.comando",
        dados={"acao": "GIRAR", "sentido": "ESQUERDA"},
        move_rodas=True,
        parametro="graus",
        maximo=180.0,
    ),
    Acao(
        nome="girar_direita_graus",
        descricao="girar no lugar VALOR graus para a direita (a bussola confere)",
        topico="navigation.comando",
        dados={"acao": "GIRAR", "sentido": "DIREITA"},
        move_rodas=True,
        parametro="graus",
        maximo=180.0,
    ),
    Acao(
        nome="desviar_esquerda_mm",
        descricao="deslocar-se VALOR mm para a esquerda e voltar ao rumo",
        topico="navigation.comando",
        dados={"acao": "DESVIAR", "lado": "ESQUERDA"},
        move_rodas=True,
        parametro="mm",
        maximo=800.0,
    ),
    Acao(
        nome="desviar_direita_mm",
        descricao="deslocar-se VALOR mm para a direita e voltar ao rumo",
        topico="navigation.comando",
        dados={"acao": "DESVIAR", "lado": "DIREITA"},
        move_rodas=True,
        parametro="mm",
        maximo=800.0,
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
        nome="manobrar",
        descricao="andar para a frente sem parada marcada, para se posicionar",
        topico="navigation.comando",
        dados={"acao": "MANUAL", "comando": "MOVE_CONTINUOUS"},
        move_rodas=True,
    ),
    Acao(
        nome="calibrar_bussola",
        descricao="refazer a referencia da bussola",
        topico="navigation.comando",
        dados={"acao": "MANUAL", "comando": "CALIBRATE_COMPASS"},
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
        if acao.delega_para and acao.topico == "navigation.comando":
            # Delegar e entregar a vez a um comportamento; mandar comando de
            # navegacao e executar por conta propria. Fazer as duas coisas
            # significaria a IA agir E soltar o controle no mesmo passo, e o
            # comportamento delegado assumiria em cima de um movimento que ja
            # esta em curso.
            raise RuntimeError(
                f"contrato violado: a acao {acao.nome!r} delega para "
                f"{acao.delega_para!r} e tambem comanda a navegacao"
            )


#: Comportamentos que o catalogo espera encontrar registrados no maestro.
#:
#: Serve de rede contra a falha que este projeto mais repete: publicar num
#: topico que ninguem escuta. `publish` nunca falha, entao nada denuncia - a
#: `Ronda` passou dias registrada e inalcancavel sem um unico erro no log.
#: Quem confere e o `motion_core/__main__.py`, que e o unico lugar que conhece
#: o catalogo E a lista do que foi registrado.
COMPORTAMENTOS_DELEGADOS: frozenset[str] = frozenset(
    acao.delega_para for acao in _CATALOGO if acao.delega_para
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
