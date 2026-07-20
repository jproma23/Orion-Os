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
        # Nivela a cabeça ao assumir. Sem isto, depois de um reset do Mega
        # (que acontece a cada reconexão serial) os servos ficam no padrão do
        # firmware - apontando para baixo - e nada os traz de volta, porque
        # o repouso não comandava pan/tilt. Prontidão é com a cabeça no nível.
        await self._event_bus.publish("motion.pan_tilt", {"pan": 0, "tilt": 0})
        logger.info("maestro: em repouso (prontidão), cabeça nivelada")
        while True:
            await asyncio.sleep(1.0)


class Ronda(Comportamento):
    """Iniciativa própria (prio 20): dar uma olhada em volta quando não há
    nada acontecendo.

    É o primeiro comportamento DISCRICIONÁRIO do Fofão - os outros são
    disparados por condição concreta (voz, alerta, obstáculo), este é uma
    escolha. Existe justamente para o conselheiro de IA ter o que escolher:
    parado (repouso) ou dar uma olhada (ronda). Com uma opção só não há
    conselho a dar.

    Só olha, NÃO anda: varre o radar e mexe o pan/tilt. Movimento de rodas
    por iniciativa da IA seria arriscado demais para o primeiro passo -
    uma olhada em volta é reversível e não machuca ninguém.
    """

    nome = "ronda"
    prioridade = 20  # acima de repouso (10), abaixo de tudo que é gatilho

    def __init__(self, event_bus: EventBus, intervalo_s: float = 60.0) -> None:
        super().__init__(event_bus)
        self._intervalo_s = intervalo_s
        self._pedida = False

    def pedir(self) -> None:
        """Chamado quando o conselheiro sugere `ronda`."""
        if not self._pedida:
            self._pedida = True
            self._reavaliar()

    def quer_rodar(self) -> bool:
        return self._pedida

    async def executar(self) -> None:
        await self._event_bus.publish("behavior.status", {"estado": "ronda"})
        logger.info("maestro: ronda - dando uma olhada em volta")
        try:
            await self._event_bus.publish("motion.pan_tilt", {"pan": -60, "tilt": 0})
            await asyncio.sleep(1.5)
            await self._event_bus.publish("motion.pan_tilt", {"pan": 60, "tilt": 0})
            await asyncio.sleep(1.5)
            await self._event_bus.publish("motion.pan_tilt", {"pan": 0, "tilt": 0})
            await self._event_bus.publish("navigation.comando", {"acao": "SCAN_FRONT"})
            await asyncio.sleep(2.0)
        finally:
            # Terminou (ou foi preemptada): a ronda não fica pendurada
            # querendo o controle de novo.
            self._pedida = False
            self._reavaliar()


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


class Vigilia(Comportamento):
    """Vigília / Modo Sentinela (prio 60): entre Atender (80) e Patrulha
    (40). Dispara ao receber `sentinela.alerta` (barulho estranho ou rosto
    desconhecido, detectados no Notebook e encaminhados ao Pi). Ao assumir:
    pede foto na direção (`sentinela.capturar_foto`), notifica e segura o
    controle por `duracao_alerta_s`, depois libera. Um alerta mais forte
    (Atender/Segurança) preempta normalmente.

    Os DETECTORES que produzem `sentinela.alerta` (anomalia de som, rosto
    desconhecido) ainda serão construídos no Notebook - este é o slot que
    reage a eles."""

    nome = "vigilia"
    prioridade = 60

    def __init__(self, event_bus: EventBus, duracao_alerta_s: float) -> None:
        super().__init__(event_bus)
        self._duracao_alerta_s = duracao_alerta_s
        self._alerta = False
        self._tipo = ""
        event_bus.subscribe("sentinela.alerta", self._ao_receber_alerta)

    async def _ao_receber_alerta(self, evento: Evento) -> None:
        self._tipo = evento.dados.get("tipo", "desconhecido")
        if not self._alerta:
            self._alerta = True
            self._reavaliar()

    def quer_rodar(self) -> bool:
        return self._alerta

    async def executar(self) -> None:
        logger.warning("maestro: VIGÍLIA - alerta '%s', investigando", self._tipo)
        await self._event_bus.publish(
            "behavior.status", {"estado": "vigilia", "tipo": self._tipo}, prioridade=Prioridade.ALTA
        )
        # pede uma foto da origem e notifica (o Notebook/interface tratam).
        await self._event_bus.publish("sentinela.capturar_foto", {"motivo": self._tipo})
        await self._event_bus.publish(
            "diagnostic.alerta", {"origem": "vigilia", "tipo": self._tipo}, prioridade=Prioridade.ALTA
        )
        # se preemptado (obstáculo/atender) durante a espera, CancelledError
        # sobe e _alerta continua True -> a Vigília RETOMA depois. Só libera
        # o controle ao concluir a investigação por completo.
        await asyncio.sleep(self._duracao_alerta_s)
        self._alerta = False
        self._reavaliar()


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
