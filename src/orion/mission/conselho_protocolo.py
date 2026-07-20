"""Protocolo do conselho de comportamento (compartilhado Pi <-> Notebook).

Vive em `orion` (e nao em `motion_core`) porque as duas pontas precisam
dele e a dependencia so pode andar num sentido: `motion_core` importa
`orion`, nunca o contrario - eles tem deploy separado (ARQUITETURA.md).

    Pi  --->  behavior.pedir_conselho  --->  Notebook
    Pi  <---  behavior.conselho        <---  Notebook
"""
from __future__ import annotations

import logging

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("orion.mission.conselho")

TOPICO_PEDIDO = "behavior.pedir_conselho"
TOPICO_RESPOSTA = "behavior.conselho"


class AtendenteConselhoIA:
    """Lado do Notebook: escuta os pedidos e responde com o conselheiro.

    Recebe uma função `aconselhar(contexto, opcoes) -> Conselho | None` em
    vez do objeto concreto, para não amarrar este módulo ao Ollama (e para
    dar para testar sem IA nenhuma).
    """

    def __init__(self, event_bus: EventBus, aconselhar) -> None:
        self._bus = event_bus
        self._aconselhar = aconselhar
        event_bus.subscribe(TOPICO_PEDIDO, self._ao_receber_pedido)

    async def _ao_receber_pedido(self, evento: Evento) -> None:
        id_pedido = evento.dados.get("id")
        try:
            conselho = await self._aconselhar(
                evento.dados.get("contexto", ""), evento.dados.get("opcoes") or []
            )
        except Exception:
            # Falha da IA não pode derrubar o Notebook nem deixar o Pi
            # esperando: melhor não responder e deixar o timeout agir.
            logger.exception("conselheiro falhou (id=%s)", id_pedido)
            return

        if conselho is None or not getattr(conselho, "aceito", False):
            logger.debug("sem conselho utilizável (id=%s)", id_pedido)
            return

        await self._bus.publish(
            TOPICO_RESPOSTA,
            {
                "id": id_pedido,
                "comportamento": conselho.comportamento,
                "motivo": conselho.motivo,
            },
        )
