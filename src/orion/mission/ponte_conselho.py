"""Ponte do Mentor de comportamento (lado Notebook), ligada em 2026-07-24.

O comportamento `Mentor` (motion_core/behavior/comportamentos.py, Raspberry)
manda um pedido de conselho via comm.request; esta ponte recebe, consulta o
`ConselheiroComportamento` (Ollama local) e responde com o comportamento
sugerido (ou nenhum). Mesmo padrao de `PonteDecisao`
(orion/mission/decisao_estrategica.py, usada pela IaEstrategica) - a
diferenca e so QUEM decide (ConselheiroComportamento local em vez de
AiManager via OpenRouter).

Nao reaproveita `orion/mission/conselho_protocolo.py`
(`AtendenteConselhoIA`): aquele modulo usa o Event Bus local puro, que nao
atravessa a rede sozinho - achado real da vistoria de codigo de
2026-07-24 (ver docs/journal.md). Esta ponte usa ComunicacaoService de
verdade, como PonteDecisao/PonteMemoria ja fazem.
"""
from __future__ import annotations

import logging

from orion.communication.protocol import Mensagem
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus, Evento
from orion.mission.conselheiro_comportamento import ConselheiroComportamento

logger = logging.getLogger("orion.mission.ponte_conselho")

COMANDO_ACONSELHAR = "behavior.aconselhar"


class PonteConselho:
    """Liga o comando `behavior.aconselhar` recebido via comm.request ao
    ConselheiroComportamento."""

    def __init__(self, conselheiro: ConselheiroComportamento, servico: ComunicacaoService) -> None:
        self._conselheiro = conselheiro
        self._servico = servico

    def registrar(self, event_bus: EventBus) -> None:
        event_bus.subscribe("comm.mensagem.command", self._ao_receber_comando)

    async def _ao_receber_comando(self, evento: Evento) -> None:
        comando = evento.dados.get("payload", {}).get("comando", "")
        if comando != COMANDO_ACONSELHAR:
            return  # nao e um pedido de conselho - outro modulo cuida disso

        mensagem_original = Mensagem.from_dict(evento.dados)
        payload = mensagem_original.payload

        try:
            conselho = await self._conselheiro.aconselhar(
                payload.get("contexto", ""),
                payload.get("opcoes") or [],
                seguranca_ativa=payload.get("seguranca_ativa", False),
            )
        except Exception:
            # Falha do conselheiro nao pode derrubar o Notebook nem deixar
            # o Pi esperando sem resposta (mesmo padrao de PonteDecisao) -
            # responde "sem conselho" em vez de deixar o timeout agir, ja
            # que aqui (diferente do AtendenteConselhoIA local) o Pi fica
            # de fato esperando um comm.request.
            logger.exception("conselheiro falhou")
            await self._servico.responder(mensagem_original, {"ok": False})
            return

        if conselho is None or not conselho.aceito:
            await self._servico.responder(mensagem_original, {"ok": False})
            return

        await self._servico.responder(
            mensagem_original,
            {"ok": True, "comportamento": conselho.comportamento, "motivo": conselho.motivo},
        )
