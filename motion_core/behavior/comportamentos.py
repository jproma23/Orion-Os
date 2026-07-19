"""Comportamentos concretos do Fofão plugados no maestro (EDR-0020).

Começo enxuto, self-contained no Pi (usa sinais que já chegam ao Event Bus
do Motion Core): Repouso (base) e VigilanciaObstaculo (segurança tática).
Outros (Atender por voz, Vigília Sentinela, Patrulha) entram depois.
"""
from __future__ import annotations

import asyncio
import logging

from motion_core.behavior.comportamento import Comportamento
from orion.kernel.event_bus import EventBus, Evento, Prioridade

logger = logging.getLogger("motion_core.behavior.comportamentos")


class Repouso(Comportamento):
    """Base da escada (prio 10): quer rodar sempre, mas perde para qualquer
    outro. Enquanto no controle, o robô está de prontidão, sem fazer nada -
    é o estado "nada acontecendo"."""

    nome = "repouso"
    prioridade = 10

    def quer_rodar(self) -> bool:
        return True  # sempre disponível; só assume quando ninguém mais quer

    async def executar(self) -> None:
        await self._event_bus.publish("behavior.status", {"estado": "repouso"})
        logger.info("maestro: em repouso (prontidão)")
        while True:
            await asyncio.sleep(1.0)


class Atender(Comportamento):
    """Atender o dono (prio 80): quando alguém chama "Fofão", o robô para o
    que estava fazendo e fica à disposição até a resposta terminar. A
    conversa em si acontece no Notebook (Voice Core); aqui o maestro apenas
    garante que o robô NÃO saia andando enquanto atende - manda HOLD ao
    Motion Core e segura o controle.

    Gatilhos vêm do Notebook, encaminhados pelo Event Bus: voice.wake_detected
    (começou) e voice.response_finished (terminou)."""

    nome = "atender"
    prioridade = 80

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self._atendendo = False
        event_bus.subscribe("voice.wake_detected", self._ao_acordar)
        event_bus.subscribe("voice.response_finished", self._ao_terminar)

    async def _ao_acordar(self, evento: Evento) -> None:
        if not self._atendendo:
            self._atendendo = True
            self._reavaliar()

    async def _ao_terminar(self, evento: Evento) -> None:
        if self._atendendo:
            self._atendendo = False
            self._reavaliar()

    def quer_rodar(self) -> bool:
        return self._atendendo

    async def executar(self) -> None:
        await self._event_bus.publish("behavior.status", {"estado": "atendendo"})
        # para o robô enquanto atende (se estava patrulhando/movendo).
        await self._event_bus.publish("navigation.comando", {"acao": "HOLD"})
        logger.info("maestro: dono chamou - atendendo (robô em espera)")
        try:
            while True:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("maestro: atendimento encerrado")
            raise


class VigilanciaObstaculo(Comportamento):
    """Segurança tática (prio 100): assume o controle quando o Hardware Core
    reporta OBSTACLE_DETECTED (via motion.status, que já chega ao Event Bus
    do Pi). Acima da camada reativa do Arduino (Cap 18), que é independente.
    Enquanto há obstáculo, segura o controle e publica o alerta; some do
    controle quando o estado sai de OBSTACLE_DETECTED."""

    nome = "vigilancia_obstaculo"
    prioridade = 100

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self._obstaculo = False
        event_bus.subscribe("motion.status", self._ao_mudar_estado)

    async def _ao_mudar_estado(self, evento: Evento) -> None:
        obstaculo_agora = evento.dados.get("estado") == "OBSTACLE_DETECTED"
        if obstaculo_agora != self._obstaculo:
            self._obstaculo = obstaculo_agora
            self._reavaliar()  # acorda o maestro para trocar de controle

    def quer_rodar(self) -> bool:
        return self._obstaculo

    async def executar(self) -> None:
        await self._event_bus.publish(
            "behavior.status", {"estado": "obstaculo"}, prioridade=Prioridade.ALTA
        )
        logger.warning("maestro: obstáculo à frente - segurança assumiu o controle")
        try:
            while True:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("maestro: obstáculo liberado - saindo da vigilância")
            raise
