"""Ponte de conselho: o maestro (Pi) pergunta, a IA (Notebook) responde.

O maestro roda no Raspberry e a IA roda no Notebook - eles não se chamam
direto (regra arquitetural #1). A conversa acontece por dois eventos:

    Pi  --->  behavior.pedir_conselho  --->  Notebook
    Pi  <---  behavior.conselho        <---  Notebook

Cada pedido leva um `id`; a resposta traz o mesmo `id` de volta. Sem isso,
uma resposta atrasada de um pedido antigo poderia ser confundida com a
resposta do pedido atual - e o robô agiria com base numa pergunta que já
não existe mais.

REGRA DE OURO: conselho é opcional. Se o Notebook estiver fora do ar, lento
ou calado, `pedir()` devolve None e o maestro segue pela regra. Nada aqui
pode segurar o laço de decisão.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from orion.kernel.event_bus import EventBus, Evento
from orion.mission.conselho_protocolo import TOPICO_PEDIDO, TOPICO_RESPOSTA

logger = logging.getLogger("motion_core.behavior.ponte_conselho")

#: Quanto o maestro espera por um conselho.
#:
#: Tem que ser MAIOR que o timeout interno do conselheiro no Notebook,
#: senão o Pi desiste antes de a resposta existir e nunca ouve nada - foi
#: exatamente o que aconteceu na primeira tentativa ao vivo (Pi esperava
#: 6s, a inferência do gemma3:1b levava 10-20s). O maestro NÃO fica travado
#: nesse tempo: a consulta roda em tarefa separada, o robô segue normal.
TIMEOUT_PADRAO_S = 25.0


class PonteConselhoIA:
    """Lado do Raspberry: faz o pedido e espera a resposta casada por id."""

    def __init__(self, event_bus: EventBus, timeout_s: float = TIMEOUT_PADRAO_S) -> None:
        self._bus = event_bus
        self._timeout_s = timeout_s
        # pedidos em voo: id -> future que a resposta vai completar
        self._pendentes: dict[str, asyncio.Future] = {}
        event_bus.subscribe(TOPICO_RESPOSTA, self._ao_receber_resposta)

    async def _ao_receber_resposta(self, evento: Evento) -> None:
        id_pedido = evento.dados.get("id")
        futuro = self._pendentes.pop(id_pedido, None)
        if futuro is None:
            # Resposta de um pedido que já venceu ou nunca existiu. Descarta
            # em silêncio - é o caso normal quando a IA responde tarde.
            logger.debug("conselho fora de hora (id=%s) - descartado", id_pedido)
            return
        if not futuro.done():
            futuro.set_result(evento.dados)

    async def pedir(self, contexto_texto: str, opcoes: list[str]) -> dict | None:
        """Pede um conselho. Devolve None se não vier a tempo.

        None NÃO é erro: significa "decida pela regra", que é o
        comportamento correto sempre que a IA não ajuda.
        """
        if not opcoes:
            return None

        id_pedido = uuid.uuid4().hex
        futuro: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pendentes[id_pedido] = futuro

        await self._bus.publish(
            TOPICO_PEDIDO,
            {"id": id_pedido, "contexto": contexto_texto, "opcoes": opcoes},
        )

        try:
            return await asyncio.wait_for(futuro, timeout=self._timeout_s)
        except asyncio.TimeoutError:
            logger.info("sem conselho em %.1fs - seguindo pela regra", self._timeout_s)
            return None
        finally:
            # Sempre limpa: pedido vencido não pode virar vazamento nem ser
            # completado depois por uma resposta atrasada.
            self._pendentes.pop(id_pedido, None)
