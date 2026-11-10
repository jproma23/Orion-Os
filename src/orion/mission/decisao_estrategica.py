"""Ponte de decisao estrategica (Cap 7, EDR-0022) - lado Notebook. ORFA.

Atendia o `mission.decidir` que o comportamento `IaEstrategica` mandava via
comm.request. **Ninguem manda mais esse comando** (conferido por grep no
repositorio inteiro em 2026-11-10, achado C5-10): a IaEstrategica do Pi
passou a usar `behavior.aconselhar` (PonteConselho), porque `decidir`
casava a resposta por substring ("parar" casa dentro de "preparar") - ver
motion_core/behavior/comportamentos.py.

Continua existindo so porque tools/conversar_jarvis.py e
orion/mission/modulo.py ainda a importam e registram; a remocao tem que ir
junto com essas duas linhas (e com tests/unit/test_decisao_estrategica.py).
"""
from __future__ import annotations

import logging

from orion.communication.protocol import Mensagem
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus, Evento
from orion.mission.ai_manager import AiManager

logger = logging.getLogger("orion.mission.decisao_estrategica")

COMANDO_DECIDIR = "mission.decidir"


class PonteDecisao:
    """Liga o comando `mission.decidir` recebido via comm.request ao AiManager."""

    def __init__(self, ai_manager: AiManager, servico: ComunicacaoService) -> None:
        self._ai_manager = ai_manager
        self._servico = servico

    def registrar(self, event_bus: EventBus) -> None:
        event_bus.subscribe("comm.mensagem.command", self._ao_receber_comando)

    async def _ao_receber_comando(self, evento: Evento) -> None:
        comando = evento.dados.get("payload", {}).get("comando", "")
        if comando != COMANDO_DECIDIR:
            return  # nao e um pedido de decisao - outro modulo cuida disso

        mensagem_original = Mensagem.from_dict(evento.dados)
        payload = mensagem_original.payload
        acoes_validas = payload.get("acoes_validas", [])

        acao = await self._ai_manager.decidir(payload.get("estado", {}), acoes_validas)

        await self._servico.responder(mensagem_original, {"ok": True, "acao": acao})
