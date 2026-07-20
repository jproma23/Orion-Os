"""Event Bus assincrono do Kernel (Cap 6 secao 5).

Toda comunicacao entre modulos passa por aqui - nenhum modulo chama outro
diretamente (regra arquitetural #1 do ARQUITETURA.txt). A entrega respeita
prioridade: eventos de prioridade mais alta sao despachados antes, mesmo que
publicados depois de eventos de prioridade mais baixa ja estarem na fila.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable

logger = logging.getLogger("orion.kernel.event_bus")


class Prioridade(IntEnum):
    """Menor valor = despachado primeiro (convencao de fila de prioridade)."""

    CRITICA = 0
    ALTA = 1
    NORMAL = 2
    BAIXA = 3


@dataclass
class Evento:
    topico: str
    dados: dict[str, Any] = field(default_factory=dict)
    prioridade: Prioridade = Prioridade.NORMAL


Handler = Callable[[Evento], "Awaitable[None] | None"]


class EventBus:
    """Barramento publish/subscribe com fila de prioridades.

    Uso tipico:
        bus = EventBus()
        bus.subscribe("system.ready", meu_handler)
        await bus.publish("system.ready", {"versao": "0.1.0"})
        asyncio.create_task(bus.iniciar())   # roda ate bus.parar()
    """

    def __init__(self) -> None:
        self._assinantes: dict[str, list[tuple[Prioridade, Handler]]] = defaultdict(list)
        self._fila: asyncio.PriorityQueue = asyncio.PriorityQueue()
        # desempate estavel: garante ordem FIFO entre eventos de mesma prioridade
        self._contador = itertools.count()
        # True desde a construcao (nao so a partir de iniciar()): se parar()
        # for chamado antes da task de iniciar() rodar sua primeira iteracao,
        # iniciar() nao pode sobrescrever essa parada de volta para True.
        self._executando = True

    def subscribe(
        self,
        topico: str,
        handler: Handler,
        prioridade: Prioridade = Prioridade.NORMAL,
    ) -> None:
        """Registra `handler` para ser chamado a cada evento publicado em `topico`."""
        self._assinantes[topico].append((prioridade, handler))

    def unsubscribe(self, topico: str, handler: Handler) -> None:
        self._assinantes[topico] = [
            (p, h) for (p, h) in self._assinantes[topico] if h is not handler
        ]

    async def publish(
        self,
        topico: str,
        dados: dict[str, Any] | None = None,
        prioridade: Prioridade = Prioridade.NORMAL,
    ) -> None:
        """Enfileira um evento para entrega assincrona aos assinantes de `topico`."""
        evento = Evento(topico=topico, dados=dados or {}, prioridade=prioridade)
        await self._fila.put((int(prioridade), next(self._contador), evento))

    async def iniciar(self) -> None:
        """Loop de despacho: consome a fila e entrega eventos aos assinantes.

        Roda ate `parar()` ser chamado. Erro em um handler e logado e isolado
        - nao derruba o bus nem os demais assinantes (Cap 6: uma falha de
        modulo nao deve derrubar o sistema inteiro).
        """
        while self._executando:
            try:
                _, _, evento = await asyncio.wait_for(self._fila.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            await self._despachar(evento)
            self._fila.task_done()

    async def _despachar(self, evento: Evento) -> None:
        assinantes = sorted(self._assinantes.get(evento.topico, []), key=lambda item: item[0])
        for _, handler in assinantes:
            try:
                resultado = handler(evento)
                if asyncio.iscoroutine(resultado):
                    await resultado
            except Exception:
                logger.exception(
                    "Erro em handler do topico '%s' (evento isolado, bus continua)",
                    evento.topico,
                )

    def parar(self) -> None:
        self._executando = False

    async def aguardar_fila_vazia(self) -> None:
        """Util em testes: espera todos os eventos enfileirados serem despachados."""
        await self._fila.join()
