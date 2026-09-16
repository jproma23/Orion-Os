"""Impulsos: de onde sai o objetivo do Fofao quando ninguem esta mandando.

## O que faltava, e o que NAO faltava

O pedido do dono (2026-08-04) foi "god mode": o robo gerando os proprios
objetivos, num laco continuo, respeitando o override humano.

Tres quartos disso ja existiam, e vale escrever quais para ninguem gastar uma
tarde reescrevendo: o laco continuo e o `BehaviorCore.executar` (reavalia a
cada 200 ms) somado ao `IaEstrategica._loop_consulta` (decide a cada 30 s); o
planejamento e o GOAP, ligado em 03/08; o override humano e o botao
AUTONOMO/MANUAL, que PREEMPTA quem estiver no controle na hora em que e
apertado.

O que nao existia e uma coisa so: **de onde vem o objetivo**. Ate aqui a
resposta era "de um modelo remoto, perguntado a cada 30 s". Com o Notebook
fora do ar, a internet caida, o timeout de 25 s estourando ou o modelo
respondendo qualquer coisa fora da lista, `escolha` vira None, o plano vira
None, e o robo fica parado - sem erro, sem excecao, sem teste vermelho. Um
robo cuja unica fonte de vontade mora na outra ponta de um cabo nao e
autonomo; e teleoperado devagar.

Este modulo e a segunda fonte: quatro familias de impulso que nascem do
PROPRIO estado do robo e nomeiam um objetivo do GOAP.

## Isto NAO e uma camada nova por cima

Empilhar camada e o defeito que este projeto mais repete, apontado pelo
proprio dono. Entao, explicitamente: os impulsos **nao decidem, nao comandam
hardware e nao tem laco proprio**. Eles produzem UM nome de objetivo, tirado
do mesmo `modelo_goap.OBJETIVOS` que a IA remota ja escolhia, e o entregam ao
mesmo planejador, dentro do mesmo ciclo que ja existia. Tudo que ja arbitra
continua arbitrando: a escada de prioridades do maestro, o modo de controle,
o contrato, e a seguranca reativa do firmware por baixo de todos.

A IA remota tambem nao sai de cena. Ela continua sendo perguntada primeiro, e
agora com os impulsos DENTRO do contexto - ela passa a saber o que o robo
esta sentindo em vez de adivinhar. Os impulsos so assumem quando ela nao
respondeu, ou respondeu fora da lista.

## A regra que mantem isto honesto

**Todo impulso nomeia um objetivo que existe, e so conta quando aquele
objetivo AINDA NAO esta satisfeito.**

As duas metades foram aprendidas caro neste repo, e nenhuma delas e teorica:

- Um impulso que nomeasse objetivo inexistente ficaria mudo para sempre sem
  nada acusar. Foi a `Ronda`, registrada no maestro e inalcancavel por dias,
  com zero execucoes e zero erros no log.
- Um impulso que continuasse pedindo o que ja esta feito produziria plano
  vazio a cada ciclo, e o robo pareceria estar decidindo sem nunca fazer
  nada. Foi o `ficar_em_seguranca` decorativo, achado em 03/08.

A primeira metade e conferida no IMPORT, por `_conferir_impulsos()` - o
Motion Core nao sobe se alguem escrever um impulso torto. A segunda e
conferida a cada avaliacao, por `goap.satisfeito()`.

## Por que existe um limiar

Sem `LIMIAR_ACAO` o impulso mais forte SEMPRE venceria, porque sempre ha um
mais forte - e o robo nunca teria sossego. O limiar e o que permite a resposta
"nao quero nada agora": abaixo dele os impulsos existem, aparecem no
dashboard e no log, e nao acionam ninguem. Repouso continua sendo um estado
legitimo do robo, e nao um bug.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import goap, modelo_goap

logger = logging.getLogger("motion_core.behavior.impulsos")

#: Abaixo disto o impulso existe, e medido e mostrado - e nao move ninguem.
#: Ver a nota sobre o limiar no cabecalho.
LIMIAR_ACAO = 0.35

# --- As quatro familias, nos nomes que o dono usou -------------------------
#: O corpo: bateria, corpo mudo. O que fala da propria maquina.
FISIOLOGIA = "fisiologia"
#: Nao saber. Cresce com o TEMPO em que o robo esta as cegas.
CURIOSIDADE = "curiosidade"
#: Perigo ja presente - caminho fechado, estranho em casa.
PROTECAO = "protecao"
#: Gente. Cresce com o tempo sozinho, e com recado esperando para ser dado.
SOCIAL = "social"

FAMILIAS: tuple[str, ...] = (FISIOLOGIA, PROTECAO, CURIOSIDADE, SOCIAL)

#: Quanto tempo as cegas ate a curiosidade chegar ao maximo.
#:
#: Cinco minutos, e nao trinta segundos: o ciclo de decisao ja e de 30 s, e um
#: impulso que satura no primeiro ciclo nao e um impulso, e uma constante. A
#: rampa existe para que "acabei de perder a medida" e "estou cego ha cinco
#: minutos" sejam coisas diferentes para quem decide.
SEGUNDOS_ATE_CURIOSIDADE_MAXIMA = 300.0

#: Quanto tempo sem ver ninguem ate a solidao chegar ao maximo.
#:
#: Quinze minutos. Mais longo que a curiosidade de proposito: nao ver ninguem
#: e o estado NORMAL de uma casa vazia durante o dia. Se saturasse rapido, o
#: robo passaria o expediente inteiro procurando gente que esta no trabalho -
#: gastaria bateria e desgastaria os servos por nada.
SEGUNDOS_ATE_SOLIDAO_MAXIMA = 900.0

#: Quanto tempo parado na frente de um obstaculo ate a urgencia chegar ao topo.
#: Curto: estar encostado numa parede e uma situacao para resolver agora, nao
#: para amadurecer.
SEGUNDOS_ATE_BLOQUEIO_MAXIMO = 60.0


@dataclass(frozen=True)
class Impulso:
    """Uma vontade do robo, e o objetivo do GOAP que a satisfaz.

    `objetivo` e o nome de um `modelo_goap.OBJETIVOS` - conferido no import.
    Nao ha objetivo novo aqui de proposito: inventar um exigiria predicados
    novos, e predicado que nenhum sensor mede nunca fica falso, entao o
    planejador o daria por resolvido para sempre e o robo pararia de tentar.
    E a nota do topo de `goap.PREDICADOS`, e vale igual aqui.
    """

    nome: str
    familia: str
    objetivo: str
    descricao: str


@dataclass(frozen=True)
class ImpulsoMedido:
    """Um impulso com a forca que ele tem AGORA, e por que."""

    impulso: Impulso
    urgencia: float
    porque: str

    @property
    def nome(self) -> str:
        return self.impulso.nome

    @property
    def familia(self) -> str:
        return self.impulso.familia

    @property
    def objetivo(self) -> str:
        return self.impulso.objetivo

    #: True quando este impulso, sozinho, ja justificaria agir.
    @property
    def forte(self) -> bool:
        return self.urgencia >= LIMIAR_ACAO

    def como_dict(self) -> dict:
        """Forma que vai para o dashboard e para o log."""
        return {
            "nome": self.nome,
            "familia": self.familia,
            "objetivo": self.objetivo,
            "urgencia": round(self.urgencia, 2),
            "porque": self.porque,
        }


# --- O catalogo ------------------------------------------------------------
#
# Poucos e de proposito, pela mesma razao dos objetivos do GOAP: impulso demais
# devolve o problema original. Se cada impulso mapeasse 1-para-1 num objetivo,
# nada teria sido ganho - so se teria trocado o nome do campo.

IMPULSOS: tuple[Impulso, ...] = (
    # --- FISIOLOGIA: o corpo --------------------------------------------
    Impulso(
        nome="corpo_mudo",
        familia=FISIOLOGIA,
        objetivo="ficar_em_seguranca",
        descricao="parei de receber noticias do meu proprio corpo",
    ),
    Impulso(
        nome="bateria_no_fim",
        familia=FISIOLOGIA,
        objetivo="ficar_em_seguranca",
        descricao="a bateria esta acabando",
    ),
    # --- PROTECAO: perigo ja presente ------------------------------------
    Impulso(
        nome="caminho_bloqueado",
        familia=PROTECAO,
        objetivo="poder_seguir_em_frente",
        descricao="tem coisa na minha frente e eu nao passo",
    ),
    Impulso(
        nome="estranho_em_casa",
        familia=PROTECAO,
        objetivo="encontrar_alguem",
        descricao="a vigilancia acusou alguem que eu nao conheco",
    ),
    # --- CURIOSIDADE: nao saber ------------------------------------------
    Impulso(
        nome="nao_sei_o_que_ha_na_frente",
        familia=CURIOSIDADE,
        objetivo="entender_o_ambiente",
        descricao="estou sem medida confiavel da frente",
    ),
    # --- SOCIAL: gente ----------------------------------------------------
    Impulso(
        nome="solidao",
        familia=SOCIAL,
        objetivo="encontrar_alguem",
        descricao="faz tempo que nao vejo ninguem",
    ),
    Impulso(
        nome="recado_na_fila",
        familia=SOCIAL,
        objetivo="encontrar_alguem",
        descricao="tenho recado guardado para entregar a alguem",
    ),
)

IMPULSOS_POR_NOME: dict[str, Impulso] = {i.nome: i for i in IMPULSOS}


def _conferir_impulsos() -> None:
    """Recusa, no IMPORT, impulso que nunca produziria nada.

    Mesma filosofia do `_conferir_contrato` e do `conferir_modelo`: falhar no
    boot, alto e claro, em vez de descobrir no quintal. O erro que isto pega e
    o silencioso - um impulso que nomeia objetivo inexistente nao levanta
    excecao em lugar nenhum, ele so nunca acontece.
    """
    for impulso in IMPULSOS:
        if impulso.objetivo not in modelo_goap.OBJETIVOS_POR_NOME:
            raise RuntimeError(
                f"impulso invalido: {impulso.nome!r} quer o objetivo "
                f"{impulso.objetivo!r}, que nao existe em modelo_goap.OBJETIVOS "
                f"- ele nunca geraria plano e nada denunciaria"
            )
        if impulso.familia not in FAMILIAS:
            raise RuntimeError(
                f"impulso invalido: {impulso.nome!r} esta na familia "
                f"{impulso.familia!r}, fora de {list(FAMILIAS)}"
            )

    # TODA FAMILIA PRECISA DE PELO MENOS UM IMPULSO.
    #
    # Nao e simetria estetica. As quatro familias sao as quatro razoes pelas
    # quais este robo age; uma familia vazia significa que uma delas foi
    # perdida numa edicao - e o robo passaria a nunca cuidar de si, ou a nunca
    # procurar gente, sem que nada no sistema tivesse mudado de forma visivel.
    vazias = [f for f in FAMILIAS if not any(i.familia == f for i in IMPULSOS)]
    if vazias:
        raise RuntimeError(
            f"familia de impulso sem nenhum impulso: {vazias} - o robo deixaria "
            f"de ter esse motivo para agir, em silencio"
        )


_conferir_impulsos()


def _rampa(decorrido: float, ate_o_maximo: float) -> float:
    """Quanto de 0 a 1, proporcional ao tempo, sem passar de 1."""
    if ate_o_maximo <= 0:
        return 1.0
    return min(1.0, max(0.0, decorrido / ate_o_maximo))


@dataclass
class _Relogio:
    """Ha quanto tempo uma condicao esta valendo (None = nao esta valendo)."""

    desde: float | None = field(default=None)

    def marcar(self, valendo: bool, agora: float) -> float:
        """Devolve ha quantos segundos a condicao vale. Zero se nao vale."""
        if not valendo:
            self.desde = None
            return 0.0
        if self.desde is None:
            self.desde = agora
        return agora - self.desde


class Impulsos:
    """Mede as quatro familias e diz qual objetivo o robo quer por si mesmo.

    Sem laco proprio e sem Event Bus: quem chama e a `IaEstrategica`, no ciclo
    que ela ja tinha. Ler o mundo aqui de novo, por conta propria, criaria uma
    segunda fonte de verdade sobre o mesmo fato - a familia de bug que mais se
    repete neste projeto.

    `contar_recados_pendentes` e opcional e segue o padrao ja usado por
    `montar_contexto` e `retrato`: sem ele o impulso `recado_na_fila` fica em
    zero, o que e a verdade (este robo nao sabe de recado nenhum) e nao uma
    falha. Deve ser um callable sincrono que devolve um inteiro.
    """

    def __init__(
        self,
        limiar_acao: float = LIMIAR_ACAO,
        contar_recados_pendentes=None,
        agora=None,
    ) -> None:
        self._limiar = limiar_acao
        self._contar_recados = contar_recados_pendentes
        #: Injetavel para o teste nao precisar dormir de verdade.
        self._agora = agora if agora is not None else time.monotonic
        self._sem_frente = _Relogio()
        self._sem_pessoa = _Relogio()
        self._bloqueado = _Relogio()
        #: Ultima linha registrada, para nao repetir a mesma frase a cada 30 s.
        #: Mesma razao do `IaEstrategica._registrar_plano`: em 04/08 o log teve
        #: 8 horas da mesma frase e foi preciso `grep -v` para ler qualquer
        #: outra coisa. Repeticao identica nao e informacao nova.
        self._ultimo_registro = ""

    # ----- medida -----

    def avaliar(self, retrato, estado: dict[str, bool]) -> list[ImpulsoMedido]:
        """Mede os sete impulsos agora, do mais urgente ao menos.

        `retrato` e o `RetratoDoMundo`; `estado` e o mesmo dicionario de
        booleanos que o planejador usa (`goap.estado_de`). Os dois, e nao um
        so: o estado ja resolveu as regras de "desconhecido vira False", e o
        retrato guarda os numeros crus de que alguns impulsos precisam para
        distinguir *nao sei* de *sei que esta ruim*.
        """
        agora = self._agora()
        medidos = [
            *self._fisiologia(retrato, estado, agora),
            *self._protecao(retrato, estado, agora),
            *self._curiosidade(retrato, estado, agora),
            *self._social(retrato, estado, agora),
        ]
        medidos.sort(key=lambda m: m.urgencia, reverse=True)
        return medidos

    def _fisiologia(self, retrato, estado, agora) -> list[ImpulsoMedido]:
        corpo_vivo = estado.get("corpo_vivo", False)
        nivel = getattr(retrato, "bateria_nivel", None)

        # A BATERIA NAO E MEDIDA NESTE ROBO HOJE (03/08): o divisor de tensao
        # nao esta montado e `bateria_nivel` e None. None vira urgencia ZERO, e
        # isso e deliberado - inventar urgencia a partir de dado que ninguem
        # produz faria o robo se recolher por medo de uma bateria imaginaria.
        # Quando o divisor for soldado, este impulso passa a funcionar sozinho,
        # sem uma linha de codigo nova.
        if nivel == "CRITICA":
            urgencia_bateria, porque_bateria = 1.0, "bateria CRITICA"
        elif nivel == "AVISO":
            urgencia_bateria, porque_bateria = 0.55, "bateria em aviso"
        else:
            urgencia_bateria = 0.0
            porque_bateria = (
                "sem medida de bateria (divisor nao montado)" if nivel is None else "bateria ok"
            )

        return [
            ImpulsoMedido(
                IMPULSOS_POR_NOME["corpo_mudo"],
                0.0 if corpo_vivo else 1.0,
                "sem telemetria do corpo" if not corpo_vivo else "corpo respondendo",
            ),
            ImpulsoMedido(IMPULSOS_POR_NOME["bateria_no_fim"], urgencia_bateria, porque_bateria),
        ]

    def _protecao(self, retrato, estado, agora) -> list[ImpulsoMedido]:
        alerta = getattr(retrato, "alerta_sentinela", None)

        # Bloqueado e diferente de cego: so conta quando o robo SABE o que ha
        # na frente e sabe que nao passa. Sem medida, quem responde e a
        # curiosidade - e a resposta certa la e olhar, nao girar as rodas.
        bloqueado = estado.get("frente_conhecida", False) and not estado.get("frente_livre", False)
        parado_ha = self._bloqueado.marcar(bloqueado, agora)
        frente = getattr(retrato, "obstaculo_frente_cm", None)

        return [
            ImpulsoMedido(
                IMPULSOS_POR_NOME["caminho_bloqueado"],
                # Comeca ja acima do limiar: um obstaculo nao precisa amadurecer
                # para ser um problema. A rampa so o leva ao topo se ninguem
                # resolver.
                0.5 + 0.5 * _rampa(parado_ha, SEGUNDOS_ATE_BLOQUEIO_MAXIMO) if bloqueado else 0.0,
                (
                    f"obstaculo a {frente:.0f} cm ha {parado_ha:.0f} s"
                    if bloqueado and frente is not None
                    else ("caminho fechado" if bloqueado else "caminho livre ou desconhecido")
                ),
            ),
            ImpulsoMedido(
                IMPULSOS_POR_NOME["estranho_em_casa"],
                0.8 if alerta else 0.0,
                f"alerta da vigilancia: {alerta}" if alerta else "nenhum alerta",
            ),
        ]

    def _curiosidade(self, retrato, estado, agora) -> list[ImpulsoMedido]:
        cego = not estado.get("frente_conhecida", False)
        ha_quanto = self._sem_frente.marcar(cego, agora)
        return [
            ImpulsoMedido(
                IMPULSOS_POR_NOME["nao_sei_o_que_ha_na_frente"],
                _rampa(ha_quanto, SEGUNDOS_ATE_CURIOSIDADE_MAXIMA) if cego else 0.0,
                f"sem medida da frente ha {ha_quanto:.0f} s" if cego else "sei o que ha na frente",
            )
        ]

    def _social(self, retrato, estado, agora) -> list[ImpulsoMedido]:
        sozinho = not estado.get("pessoa_visivel", False)
        ha_quanto = self._sem_pessoa.marcar(sozinho, agora)

        # Falha ao contar recado NAO derruba a avaliacao: os outros oito
        # impulsos continuam validos, e um robo que para de querer qualquer
        # coisa porque o banco engasgou e pior do que um robo sem esse impulso.
        pendentes = 0
        if self._contar_recados is not None:
            try:
                pendentes = int(self._contar_recados() or 0)
            except Exception:
                logger.warning("nao consegui contar os recados pendentes", exc_info=True)

        return [
            ImpulsoMedido(
                IMPULSOS_POR_NOME["solidao"],
                _rampa(ha_quanto, SEGUNDOS_ATE_SOLIDAO_MAXIMA) if sozinho else 0.0,
                f"sem ver ninguem ha {ha_quanto:.0f} s" if sozinho else "tem gente aqui",
            ),
            ImpulsoMedido(
                IMPULSOS_POR_NOME["recado_na_fila"],
                0.75 if pendentes else 0.0,
                f"{pendentes} recado(s) para entregar" if pendentes else "nenhum recado guardado",
            ),
        ]

    # ----- decisao -----

    def objetivo_interno(self, retrato, estado: dict[str, bool]) -> tuple[str, str] | None:
        """O objetivo que o robo quer POR SI MESMO, ou None se nao quer nada.

        Devolve `(nome_do_objetivo, motivo)`, no mesmo formato que a resposta
        da IA remota ja tinha - de proposito, para o resto do caminho nao
        precisar saber de onde veio a vontade.

        Descarta, nesta ordem:

        1. impulso fraco (abaixo do limiar) - repouso e resposta legitima;
        2. impulso cujo objetivo JA ESTA SATISFEITO. Este e o descarte que
           impede o robo de "decidir" a cada 30 s sem nunca fazer nada, que foi
           exatamente o defeito do `ficar_em_seguranca` decorativo. Um impulso
           forte cujo objetivo ja esta feito nao e uma vontade, e um alivio.

        Nao ha desempate por familia. Se dois impulsos empatam, vence o
        primeiro da lista ordenada - e a ordem entre iguais nao importa porque
        varios impulsos apontam para o MESMO objetivo de proposito (dois deles
        pedem `ficar_em_seguranca`). O que interessa e o objetivo escolhido,
        nao qual das razoes o justificou.
        """
        for medido in self.avaliar(retrato, estado):
            if medido.urgencia < self._limiar:
                # A lista vem ordenada: o primeiro fraco significa que todos os
                # seguintes tambem sao.
                break
            objetivo = modelo_goap.OBJETIVOS_POR_NOME[medido.objetivo]
            if goap.satisfeito(estado, objetivo):
                continue
            motivo = f"impulso proprio: {medido.impulso.descricao} ({medido.porque})"
            self._registrar(
                f"impulso: '{medido.nome}' ({medido.familia}, urgencia {medido.urgencia:.2f}) "
                f"-> objetivo '{medido.objetivo}' - {medido.porque}"
            )
            return medido.objetivo, motivo

        self._registrar("impulso: nenhum forte o bastante agora - sem vontade propria")
        return None

    def _registrar(self, texto: str) -> None:
        """Registra sem repetir a mesma frase em laco (ver `_ultimo_registro`)."""
        if texto == self._ultimo_registro:
            logger.debug("%s (repetido)", texto)
            return
        self._ultimo_registro = texto
        logger.info("%s", texto)

    # ----- para o prompt e para o dashboard -----

    def bloco_para_prompt(self, medidos: list[ImpulsoMedido]) -> str:
        """As vontades do robo em texto, para entrar no contexto da IA remota.

        POR QUE A IA REMOTA PRECISA DISTO. Sem este bloco ela recebe o quadro
        do mundo (distancia, bateria, quem esta na sala) mas nada sobre o que
        aquilo tudo significa para o robo - ela teria que reconstruir a cada
        ciclo, do zero, que ha 12 minutos ninguem aparece. O tempo e justamente
        o que um retrato instantaneo nao carrega.

        So os impulsos com alguma forca entram. Listar nove linhas em que sete
        dizem "zero" gastaria contexto para dizer que nada esta acontecendo.
        """
        fortes = [m for m in medidos if m.urgencia > 0.0]
        if not fortes:
            return "o que eu estou sentindo: nada em especial agora"
        linhas = [
            f"- {m.familia}: {m.impulso.descricao} ({m.porque}) "
            f"[urgencia {m.urgencia:.2f}{', forte' if m.forte else ''}]"
            for m in fortes
        ]
        return "o que eu estou sentindo:\n" + "\n".join(linhas)
