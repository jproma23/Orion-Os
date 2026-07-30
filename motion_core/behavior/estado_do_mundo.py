"""Modelo de mundo unificado do Fofão (base da integração cognitiva).

PROBLEMA QUE RESOLVE
--------------------
Até aqui cada comportamento do maestro assinava seus próprios eventos e via
só um pedaço da realidade: `Vigilia` sabia de rostos mas não de bateria,
`VigilanciaObstaculo` sabia de obstáculo mas não de quem estava na frente.
Decisão boa exige o quadro inteiro.

Esta classe assina os tópicos do Event Bus (regra arquitetural #1: ninguém
chama ninguém direto) e mantém UM retrato coerente do mundo, que qualquer
comportamento consulta.

O PONTO MAIS IMPORTANTE: DADO VELHO NÃO É DADO
----------------------------------------------
Um modelo de mundo ingênuo guarda o último valor recebido para sempre. Isso
é perigoso: se a telemetria parar (cabo solto, Mega resetando), o quadro
continuaria dizendo "obstáculo a 34cm, tudo ok" com informação de cinco
minutos atrás, e o maestro decidiria em cima de ficção.

Aqui cada dado tem prazo de validade (`validade_s`). Passou do prazo, o
campo responde `None` - "eu não sei" - em vez de mentir um valor velho.
Quem consulta precisa tratar `None`, e isso é de propósito: força quem
decide a encarar a incerteza em vez de ignorá-la.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("motion_core.behavior.estado_do_mundo")

# Prazo de validade por família de dado. Vem do ritmo real de cada fonte:
# a telemetria do Mega chega a cada 500ms, então 3s já significa "parou de
# chegar". Visão e voz são naturalmente esporádicas e valem por mais tempo.
VALIDADE_TELEMETRIA_S = 3.0
VALIDADE_ESTADO_HARDWARE_S = 5.0
VALIDADE_VISAO_S = 15.0
VALIDADE_VOZ_S = 30.0
#: Por quanto tempo "andei sem medida confiavel" continua sendo noticia para
#: quem decide. 30s cobre alguns segmentos de movimento; passado isso, o fato
#: envelheceu e o silencio volta a ser a resposta honesta.
VALIDADE_RISCO_S = 30.0


@dataclass
class _Campo:
    """Um valor com o instante em que chegou."""

    valor: Any = None
    instante: float = 0.0

    def ler(self, validade_s: float, agora: float | None = None) -> Any:
        """Devolve o valor, ou None se estiver velho demais para confiar."""
        if self.instante == 0.0:
            return None  # nunca chegou nada
        agora = time.monotonic() if agora is None else agora
        if agora - self.instante > validade_s:
            return None  # venceu
        return self.valor

    def escrever(self, valor: Any) -> None:
        self.valor = valor
        self.instante = time.monotonic()


@dataclass(frozen=True)
class RetratoDoMundo:
    """Foto imutável do mundo num instante, para quem for decidir.

    Todo campo pode ser None e isso SEMPRE significa "não sei" - ou nunca
    chegou, ou o dado venceu. Nunca significa "zero" nem "tudo bem".
    """

    # --- corpo do robô (telemetria do Mega) ---
    obstaculo_frente_cm: float | None
    obstaculo_tras_cm: float | None
    inclinacao_graus: float | None
    impacto: bool | None
    bateria_v: float | None
    bateria_nivel: str | None

    # --- situação (estado do Hardware/Motion Core) ---
    estado_hardware: str | None

    # --- mundo lá fora (visão, no Notebook) ---
    pessoa_presente: bool | None
    pessoa_nome: str | None
    pessoa_conhecida: bool | None
    alerta_sentinela: str | None

    # --- interação (voz) ---
    ouvindo: bool

    # --- meta ---
    telemetria_viva: bool  # False = paramos de receber notícias do corpo

    #: True quando o robô avançou HÁ POUCO sem medida frontal confiável.
    #:
    #: Nasce da decisão do dono de 2026-07-30: *"travas de segurança só
    #: impedem o robô de andar - retira as travas, temos os sensores para
    #: realizar estudos de risco e tomada de decisão"*. A navegação deixou de
    #: recusar o avanço na dúvida; em troca, ela DECLARA o que fez. Este campo
    #: é onde essa declaração chega a quem decide.
    #:
    #: Sem ele, o `navigation.risco_assumido` seria mais um evento publicado
    #: no vazio - e o defeito que este projeto mais repete é exatamente esse.
    #: Default False para não quebrar quem constrói o retrato à mão (testes).
    risco_frontal_assumido: bool = False

    def resumo(self) -> str:
        """Uma linha legível - útil em log e para explicar decisão."""
        def fmt(v: Any, sufixo: str = "") -> str:
            return "?" if v is None else f"{v}{sufixo}"

        return (
            f"frente={fmt(self.obstaculo_frente_cm, 'cm')} "
            f"inclin={fmt(self.inclinacao_graus, 'gr')} "
            f"bat={fmt(self.bateria_nivel)} "
            f"estado={fmt(self.estado_hardware)} "
            f"pessoa={fmt(self.pessoa_nome)} "
            f"ouvindo={self.ouvindo} "
            f"corpo={'vivo' if self.telemetria_viva else 'SEM SINAL'}"
        )


class EstadoDoMundo:
    """Funde os eventos do barramento num retrato único e datado."""

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus

        self._telemetria = _Campo()
        self._estado_hardware = _Campo()
        self._pessoa = _Campo()
        self._alerta = _Campo()
        self._risco_frente = _Campo()
        self._voz_ativa = False

        # Corpo do robô: telemetria periódica vinda do Mega pela ponte serial.
        event_bus.subscribe("comm.mensagem.telemetry", self._ao_receber_telemetria)
        # Situação do Hardware Core (IDLE / OBSTACLE_DETECTED / SAFE_MODE...).
        event_bus.subscribe("motion.status", self._ao_receber_estado)
        # Mundo lá fora: visão roda no Notebook e chega encaminhada.
        #
        # DOIS eventos, de propósito (corrigido em 2026-07-29): eles respondem
        # perguntas diferentes e só juntos dão o quadro.
        #
        #   person_detected   -> "tem ALGUÉM aí" (qualquer pessoa que o YOLO vê)
        #   person_recognized -> "é o João Paulo" (só quando o rosto casa com
        #                        alguém cadastrado)
        #
        # Até esta data só o primeiro era assinado, e `_ao_ver_pessoa` lia
        # `evento.dados["nome"]` dele - campo que aquele evento NUNCA carregou
        # (o payload real é `{classe, confianca, caixa, timestamp}`). Então
        # `pessoa_conhecida` era falso para todo mundo, inclusive para os donos
        # da casa, e a IA decidia achando que só entravam estranhos.
        event_bus.subscribe("vision.person_detected", self._ao_ver_pessoa)
        event_bus.subscribe("vision.person_recognized", self._ao_reconhecer_pessoa)
        event_bus.subscribe("vision.person_lost", self._ao_perder_pessoa)
        event_bus.subscribe("sentinela.alerta", self._ao_receber_alerta)
        # A navegação declarando que andou sem saber o que havia à frente.
        # CONFERIDO ANTES DE ASSINAR (a regra que este projeto aprendeu a
        # duras penas): `navigation.risco_assumido` é publicado pelo
        # `NavigationCore`, que roda NESTE processo e neste mesmo barramento -
        # não precisa de encaminhamento e não vira órfão.
        event_bus.subscribe("navigation.risco_assumido", self._ao_assumir_risco)
        # Interação por voz.
        event_bus.subscribe("voice.wake_detected", self._ao_acordar_voz)
        event_bus.subscribe("voice.response_finished", self._ao_terminar_voz)

    # ----- entrada de eventos -----

    async def _ao_receber_telemetria(self, evento: Evento) -> None:
        payload = evento.dados.get("payload") or {}
        self._telemetria.escrever(payload)

        # O ESTADO DO CORPO VEM DA TELEMETRIA TAMBÉM (2026-07-30).
        #
        # É a mesma correção que o `NavigationCore._ao_atualizar_telemetria`
        # recebeu em 29/07, e que este arquivo não recebeu junto. O
        # `motion.status` (abaixo) só é publicado pelo firmware QUANDO O
        # ESTADO MUDA - com o Mega parado em OBSTACLE_DETECTED não há
        # transição nenhuma para anunciar, o campo vence por idade e o retrato
        # passa a dizer `None`.
        #
        # A consequência era visível em produção, uma linha por ciclo no
        # prompt de quem decide as manobras:
        #
        #     - estado do corpo: não sei (sem registro)
        #
        # A IA precisava escolher a manobra sem saber se o corpo estava
        # ocioso, executando uma missão ou travado num obstáculo - e o dado
        # certo chegava aqui a cada segundo, dentro deste mesmo payload, sem
        # ninguém ler. A telemetria é o lastro que corrige a deriva; o
        # `motion.status` continua servindo para reagir na hora da transição.
        estado = payload.get("estado")
        if estado:
            self._estado_hardware.escrever(estado)

    async def _ao_receber_estado(self, evento: Evento) -> None:
        estado = evento.dados.get("estado")
        if estado is not None:
            self._estado_hardware.escrever(estado)

    async def _ao_ver_pessoa(self, evento: Evento) -> None:
        """Alguém apareceu. Este evento NÃO diz quem - ver _ao_reconhecer_pessoa.

        Não sobrescreve um nome já conhecido: a visão publica `person_detected`
        a cada quadro para cada pessoa, e `person_recognized` só quando o rosto
        é identificado. Se este handler apagasse o nome, a identidade duraria
        um quadro e o robô esqueceria quem está na frente dele a 15 vezes por
        segundo.
        """
        atual = self._pessoa.ler(VALIDADE_VISAO_S) or {}
        self._pessoa.escrever(
            {
                "nome": atual.get("nome"),
                "conhecida": bool(atual.get("nome")),
            }
        )

    async def _ao_reconhecer_pessoa(self, evento: Evento) -> None:
        """Agora sim sabemos QUEM é - só este evento carrega `nome`."""
        nome = evento.dados.get("nome")
        self._pessoa.escrever({"nome": nome, "conhecida": bool(nome)})

    async def _ao_perder_pessoa(self, evento: Evento) -> None:
        # Vence na hora: sumiu de vista, não há mais ninguém presente.
        self._pessoa = _Campo()

    async def _ao_receber_alerta(self, evento: Evento) -> None:
        self._alerta.escrever(evento.dados.get("tipo", "desconhecido"))

    async def _ao_assumir_risco(self, evento: Evento) -> None:
        """O robô andou sem medida confiável - e agora quem decide sabe."""
        logger.info(
            "estado do mundo: risco assumido em '%s' (%s)",
            evento.dados.get("contexto", "?"),
            evento.dados.get("motivo", "?"),
        )
        self._risco_frente.escrever(True)

    async def _ao_acordar_voz(self, evento: Evento) -> None:
        self._voz_ativa = True

    async def _ao_terminar_voz(self, evento: Evento) -> None:
        self._voz_ativa = False

    # ----- leitura -----

    def retrato(self) -> RetratoDoMundo:
        """Monta a foto do mundo agora, já descartando o que venceu."""
        agora = time.monotonic()
        tele = self._telemetria.ler(VALIDADE_TELEMETRIA_S, agora) or {}
        viva = self._telemetria.ler(VALIDADE_TELEMETRIA_S, agora) is not None
        pessoa = self._pessoa.ler(VALIDADE_VISAO_S, agora)

        def do_mega(
            chave: str,
            chave_valido: str | None = None,
            chave_sem_eco: str | None = None,
        ) -> Any:
            """Lê campo da telemetria respeitando os flags de validade dele.

            O firmware manda pares (valor, valor_valido) - um ultrassom sem
            eco devolve valor sujo com o flag em False. Ignorar o flag aqui
            reintroduziria exatamente o tipo de mentira que esta classe
            existe para evitar.

            `chave_sem_eco` cobre um caso que o `valido` NÃO cobre, achado em
            2026-07-29: quando nada reflete, `_foraDeAlcance` (no
            sensor_ultrassonico.h) reporta o TETO DE ALCANCE - 517 cm - com
            `valido: true`. É "não medi" entregue como "medi 5 metros de
            espaço livre". O próprio driver marca `*_sem_eco` para denunciar
            isso, e até esta data ninguém no sistema olhava a bandeira.

            Por que importa mais do que parece: o eco de um obstáculo a 40 cm
            dura ~2,3 ms e o loop do Mega fica cego por até 124 ms. O eco
            inteiro cabe dentro da cegueira, e o driver conclui "nada
            refletiu". Ou seja, quanto MAIS PERTO o obstáculo, maior a chance
            de ele virar "caminho livre" - a falha aponta para o lado errado
            justamente na hora que importa.
            """
            if not viva:
                return None
            if chave_valido is not None and not tele.get(chave_valido):
                return None
            if chave_sem_eco is not None and tele.get(chave_sem_eco):
                return None  # não é medição - "não sei", nunca "está livre"
            return tele.get(chave)

        return RetratoDoMundo(
            obstaculo_frente_cm=do_mega(
                "distancia_frontal_cm", "distancia_frontal_valida", "frontal_sem_eco"
            ),
            obstaculo_tras_cm=do_mega(
                "distancia_traseira_cm", "distancia_traseira_valida", "traseiro_sem_eco"
            ),
            inclinacao_graus=do_mega("inclinacao_graus"),
            impacto=do_mega("impacto_detectado"),
            bateria_v=do_mega("bateria_tensao_v", "bateria_lida"),
            bateria_nivel=do_mega("bateria_nivel", "bateria_lida"),
            estado_hardware=self._estado_hardware.ler(VALIDADE_ESTADO_HARDWARE_S, agora),
            pessoa_presente=None if pessoa is None else True,
            pessoa_nome=None if pessoa is None else pessoa.get("nome"),
            pessoa_conhecida=None if pessoa is None else pessoa.get("conhecida"),
            alerta_sentinela=self._alerta.ler(VALIDADE_VISAO_S, agora),
            ouvindo=self._voz_ativa,
            telemetria_viva=viva,
            risco_frontal_assumido=bool(self._risco_frente.ler(VALIDADE_RISCO_S, agora)),
        )
