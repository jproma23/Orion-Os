"""O que cada acao do contrato EXIGE e o que ela MUDA - o modelo do GOAP.

## Por que isto nao mora dentro do contrato.py

Precondicao e efeito pertencem, em principio, junto da acao. Ficaram separados
aqui por uma razao concreta: `contrato.py` responde *"o que a IA tem
autoridade para fazer?"* e e o arquivo que alguem le quando quer auditar o
limite do robo. Enfiar modelagem de planejamento no meio das 16 entradas
dobraria o tamanho daquele arquivo e afogaria a pergunta que ele existe para
responder.

O preco dessa separacao e o risco classico de dois lugares saindo de sincronia
- alguem acrescenta uma acao no catalogo e esquece dela aqui. Esse preco esta
PAGO: `_conferir_cobertura()` roda no import e derruba o Motion Core se
qualquer acao do catalogo nao tiver entrada aqui. Nao e disciplina, e trava.

## Como ler uma entrada

    "avancar": (
        {"corpo_vivo": True, "frente_livre": True},   # so posso se...
        {},                                           # ...e o mundo fica...
    )

Efeito vazio nao e esquecimento: e uma acao que muda o mundo fisico sem mudar
nenhum PREDICADO que o robo sabe medir. `avancar` anda para a frente, mas
depois de andar a frente continua conhecida e livre (ou nao) conforme o
sensor disser - nao ha o que prometer.
"""
from __future__ import annotations

from . import contrato
from .goap import Objetivo, conferir_modelo

#: acao -> (precondicoes, efeitos)
_MODELO: dict[str, tuple[dict[str, bool], dict[str, bool]]] = {
    # --- Descobrir o mundo -------------------------------------------------
    # As duas acoes que fecham `frente_conhecida`. Sao a porta de entrada de
    # quase todo plano, porque quase tudo depende de saber o que ha na frente.
    "observar_ambiente": ({"corpo_vivo": True}, {"frente_conhecida": True}),
    # Olhar em volta gira a cabeca por todo o arco: alem da frente, e a unica
    # acao que pode ENCONTRAR uma pessoa que nao estava no campo de visao.
    # E ela que faz o plano de dois passos "olhar_em_volta -> seguir_pessoa"
    # existir - o caso que o sistema de acao unica nunca conseguiu produzir.
    "olhar_em_volta": (
        {"corpo_vivo": True},
        {"frente_conhecida": True, "pessoa_visivel": True},
    ),
    # --- Mover ------------------------------------------------------------
    # Todas exigem `sem_impacto` e `nivelado`: planejar movimento com o robo
    # tombado ou logo apos uma batida e planejar para um mundo que nao e o
    # que esta acontecendo. A seguranca do firmware nao veta mais (EDR-0027),
    # entao esta e a camada onde esse juizo tem que existir.
    "explorar": (
        {"corpo_vivo": True, "frente_livre": True, "nivelado": True, "sem_impacto": True, "bateria_ok": True},
        {},
    ),
    "avancar": (
        {"corpo_vivo": True, "frente_livre": True, "nivelado": True, "sem_impacto": True, "bateria_ok": True},
        {},
    ),
    # GIRAR ZERA `frente_conhecida` DE PROPOSITO, e este e o detalhe mais
    # importante do modelo. Depois de girar, o robo esta olhando para outro
    # lugar: a medida que ele tinha vale para uma direcao que nao e mais a
    # dele. Sem este efeito negativo, o planejador acharia que continua
    # sabendo o que ha na frente e montaria planos sobre um dado vencido.
    #
    # E `frente_livre: True` e a INTENCAO de girar procurando saida - nao
    # promessa. Se depois do giro a frente continuar bloqueada, o proximo
    # ciclo replaneja. Ver a nota sobre intencao em goap.py.
    "virar_esquerda": (
        {"corpo_vivo": True, "frente_conhecida": True, "nivelado": True, "sem_impacto": True, "bateria_ok": True},
        {"frente_livre": True, "frente_conhecida": False},
    ),
    "virar_direita": (
        {"corpo_vivo": True, "frente_conhecida": True, "nivelado": True, "sem_impacto": True, "bateria_ok": True},
        {"frente_livre": True, "frente_conhecida": False},
    ),
    "manobrar": (
        {"corpo_vivo": True, "frente_livre": True, "nivelado": True, "sem_impacto": True, "bateria_ok": True},
        {},
    ),
    "seguir_pessoa": (
        {"corpo_vivo": True, "pessoa_visivel": True, "nivelado": True, "sem_impacto": True, "bateria_ok": True},
        {},
    ),
    # `parar` nao exige nada: e a unica acao que continua valendo com o corpo
    # mudo, tombado ou depois de uma batida. Uma acao sem precondicao alguma
    # e o piso do planejador.
    #
    # O EFEITO `parado` FOI ACRESCENTADO EM 2026-08-03, e conserta um objetivo
    # que nao funcionava. `ficar_em_seguranca` pedia `sem_impacto: True`, e
    # NENHUMA acao produz esse predicado - impacto e leitura de sensor, nao
    # consequencia de decisao. Varrendo os 256 mundos possiveis, aquele
    # objetivo nao gerava plano em nenhum: ou ja estava satisfeito (plano
    # vazio) ou era impossivel (None).
    #
    # O tamanho do problema so aparece com o GOAP ligado: a IA escolhe entre 4
    # objetivos, e um deles se chama "ficar em seguranca". E o nome mais
    # atraente justamente quando as coisas vao mal - e escolhe-lo fazia o robo
    # nao fazer nada, sem erro, sem log, sem nada denunciando.
    #
    # `parado` sai de `estado_hardware == "IDLE"`, que a telemetria reporta -
    # respeita a regra de que todo efeito tem que ser observavel no retrato.
    "parar": ({}, {"parado": True}),
    # --- Cuidar de si -----------------------------------------------------
    # Calibrar exige o robo PARADO na pratica, mas isso nao e um predicado do
    # vocabulario e nao vale inventar um que ninguem mede (ver PREDICADOS em
    # goap.py). O que da para exigir com honestidade e corpo vivo e nivelado -
    # calibrar a inclinacao com o robo tombado gravaria a referencia errada.
    "calibrar_inclinacao": ({"corpo_vivo": True, "nivelado": True}, {}),
    "calibrar_bussola": ({"corpo_vivo": True, "sem_impacto": True}, {}),
    # --- Perifericos ------------------------------------------------------
    # Nenhum destes muda predicado nenhum: nao existe "luz_acesa" no
    # RetratoDoMundo, entao nao existe aqui. Continuam alcancaveis pela IA
    # como acao direta; o que elas nao sao e degrau de plano.
    "acender_luz": ({"corpo_vivo": True}, {}),
    "apagar_luz": ({"corpo_vivo": True}, {}),
    "ligar_ventilador": ({"corpo_vivo": True}, {}),
    "desligar_ventilador": ({"corpo_vivo": True}, {}),
    "fotografar": ({}, {}),
}


class AcaoPlanejavel:
    """Uma acao do contrato vestida com precondicoes e efeitos.

    Envolve em vez de herdar porque `contrato.Acao` e `frozen=True`: a
    imutabilidade la e proposital (o catalogo e limite de autoridade, nao
    estrutura de trabalho) e nao se mexe nela para conveniencia daqui.
    """

    __slots__ = ("acao", "precondicoes", "efeitos")

    def __init__(self, acao, precondicoes, efeitos) -> None:
        self.acao = acao
        self.precondicoes = precondicoes
        self.efeitos = efeitos

    @property
    def nome(self) -> str:
        return self.acao.nome

    def __repr__(self) -> str:
        return f"<AcaoPlanejavel {self.acao.nome}>"


ACOES_PLANEJAVEIS: tuple[AcaoPlanejavel, ...] = tuple(
    AcaoPlanejavel(contrato.ACOES[nome], pre, efe)
    for nome, (pre, efe) in _MODELO.items()
    if nome in contrato.ACOES
)


#: O que a IA passa a escolher, no lugar de uma acao solta.
#:
#: Sao poucos de proposito. Objetivo demais devolve o problema original - se a
#: IA tiver 16 objetivos que mapeiam 1-para-1 nas 16 acoes, nada foi ganho,
#: so se trocou o nome do campo. Estes quatro sao os que produzem PLANO: cada
#: um pode exigir passos que a IA nao precisou enumerar.
OBJETIVOS: tuple[Objetivo, ...] = (
    Objetivo(
        nome="entender_o_ambiente",
        descricao="saber o que ha ao meu redor",
        alvo={"frente_conhecida": True},
    ),
    Objetivo(
        nome="poder_seguir_em_frente",
        descricao="ter caminho livre para andar",
        alvo={"frente_livre": True},
    ),
    Objetivo(
        nome="encontrar_alguem",
        descricao="achar uma pessoa para acompanhar ou atender",
        alvo={"pessoa_visivel": True},
    ),
    Objetivo(
        nome="ficar_em_seguranca",
        descricao="parar e nao piorar a situacao",
        # `parado`, e nao `sem_impacto` (corrigido em 2026-08-03): o alvo tem
        # que ser algo que uma acao ALCANCE. Ninguem "faz" o impacto sumir -
        # ele e leitura de sensor. O que o robo pode fazer para nao piorar a
        # situacao e exatamente o que o nome do objetivo diz: parar.
        alvo={"parado": True},
    ),
)

OBJETIVOS_POR_NOME: dict[str, Objetivo] = {alvo.nome: alvo for alvo in OBJETIVOS}

#: Nomes que vao no prompt, para a IA escolher. Espelha `contrato.NOMES`.
NOMES_OBJETIVOS: tuple[str, ...] = tuple(OBJETIVOS_POR_NOME)


def _conferir_cobertura() -> None:
    """Toda acao do catalogo tem que estar modelada aqui, e vice-versa.

    E a trava que paga o preco de o modelo morar fora do contrato.py (ver o
    cabecalho). Sem ela, acrescentar uma acao nova ao catalogo a tornaria
    invisivel ao planejador - ela existiria para a IA escolher direto e nunca
    apareceria dentro de um plano, sem nada denunciando a diferenca.
    """
    do_catalogo = set(contrato.ACOES)
    do_modelo = set(_MODELO)
    faltando = do_catalogo - do_modelo
    if faltando:
        raise RuntimeError(
            f"modelo GOAP incompleto: {sorted(faltando)!r} esta(o) no catalogo "
            f"do contrato mas sem precondicoes/efeitos aqui - o planejador "
            f"nunca as usaria"
        )
    sobrando = do_modelo - do_catalogo
    if sobrando:
        raise RuntimeError(
            f"modelo GOAP com acao fantasma: {sorted(sobrando)!r} nao existe "
            f"em contrato.ACOES"
        )

    # TODA acao que move as rodas exige as mesmas quatro condicoes.
    #
    # Isto nasceu de um erro meu, pego pelo teste em 2026-08-02: `explorar`,
    # `avancar` e `seguir_pessoa` exigiam `bateria_ok`, mas `virar_esquerda`,
    # `virar_direita` e `manobrar` nao - e as tres tambem movem as rodas. Com
    # bateria critica o planejador continuava mandando o robo girar.
    #
    # A licao nao e "faltou atencao", e que uma regra que so existe na cabeca
    # de quem escreve volta a ser quebrada na proxima acao acrescentada.
    # Escrita aqui, ela vira condicao de boot: quem puser uma acao de roda
    # sem estas precondicoes descobre no import, nao no quintal.
    #
    # `move_rodas` e o mesmo campo que o contrato ja usa para exigir que
    # movimento passe pela navegacao (regra 2) - a fonte da verdade sobre "o
    # que mexe no robo fisicamente" continua sendo uma so.
    exigidas = {"corpo_vivo", "nivelado", "sem_impacto", "bateria_ok"}
    for nome, (precondicoes, _) in _MODELO.items():
        acao = contrato.ACOES[nome]
        if not acao.move_rodas:
            continue
        faltando_pre = {c for c in exigidas if precondicoes.get(c) is not True}
        if faltando_pre:
            raise RuntimeError(
                f"modelo GOAP inseguro: a acao {nome!r} move as rodas mas nao "
                f"exige {sorted(faltando_pre)!r} - o planejador a usaria com o "
                f"robo tombado, sem corpo, apos batida ou com a bateria no fim"
            )


_conferir_cobertura()
conferir_modelo(ACOES_PLANEJAVEIS, OBJETIVOS)


def resolver_objetivo(nome: str | None) -> Objetivo | None:
    """Traduz a escolha da IA num objetivo, ou None para abstencao.

    Mesma regra 6 do contrato: fora da lista nunca vira chute.
    """
    if not nome:
        return None
    limpo = nome.strip().lower()
    if limpo == contrato.ABSTENCAO:
        return None
    return OBJETIVOS_POR_NOME.get(limpo)
