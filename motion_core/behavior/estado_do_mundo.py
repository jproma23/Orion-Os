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
from dataclasses import dataclass, field
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
        self._voz_ativa = False

        # Corpo do robô: telemetria periódica vinda do Mega pela ponte serial.
        event_bus.subscribe("comm.mensagem.telemetry", self._ao_receber_telemetria)
        # Situação do Hardware Core (IDLE / OBSTACLE_DETECTED / SAFE_MODE...).
        event_bus.subscribe("motion.status", self._ao_receber_estado)
        # Mundo lá fora: visão roda no Notebook e chega encaminhada.
        event_bus.subscribe("vision.person_detected", self._ao_ver_pessoa)
        event_bus.subscribe("vision.person_lost", self._ao_perder_pessoa)
        event_bus.subscribe("sentinela.alerta", self._ao_receber_alerta)
        # Interação por voz.
        event_bus.subscribe("voice.wake_detected", self._ao_acordar_voz)
        event_bus.subscribe("voice.response_finished", self._ao_terminar_voz)

    # ----- entrada de eventos -----

    async def _ao_receber_telemetria(self, evento: Evento) -> None:
        payload = evento.dados.get("payload") or {}
        self._telemetria.escrever(payload)

    async def _ao_receber_estado(self, evento: Evento) -> None:
        estado = evento.dados.get("estado")
        if estado is not None:
            self._estado_hardware.escrever(estado)

    async def _ao_ver_pessoa(self, evento: Evento) -> None:
        self._pessoa.escrever(
            {
                "nome": evento.dados.get("nome"),
                # Sem nome = rosto que a visão não reconheceu.
                "conhecida": bool(evento.dados.get("nome")),
            }
        )

    async def _ao_perder_pessoa(self, evento: Evento) -> None:
        # Vence na hora: sumiu de vista, não há mais ninguém presente.
        self._pessoa = _Campo()

    async def _ao_receber_alerta(self, evento: Evento) -> None:
        self._alerta.escrever(evento.dados.get("tipo", "desconhecido"))

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

        def do_mega(chave: str, chave_valido: str | None = None) -> Any:
            """Lê campo da telemetria respeitando o flag de validade dele.

            O firmware manda pares (valor, valor_valido) - um ultrassom sem
            eco devolve valor sujo com o flag em False. Ignorar o flag aqui
            reintroduziria exatamente o tipo de mentira que esta classe
            existe para evitar.
            """
            if not viva:
                return None
            if chave_valido is not None and not tele.get(chave_valido):
                return None
            return tele.get(chave)

        return RetratoDoMundo(
            obstaculo_frente_cm=do_mega("distancia_frontal_cm", "distancia_frontal_valida"),
            obstaculo_tras_cm=do_mega("distancia_traseira_cm", "distancia_traseira_valida"),
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
        )
