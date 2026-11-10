"""Ponte do Mentor de comportamento (lado Notebook), ligada em 2026-07-24.

O comportamento `IaEstrategica` (motion_core/behavior/comportamentos.py,
Raspberry) manda um pedido de conselho via comm.request; esta ponte recebe,
consulta o `ConselheiroComportamento` (IA remota com saida JSON fechada) e
responde com o comportamento sugerido (ou nenhum).

Nao reaproveita `orion/mission/conselho_protocolo.py`
(`AtendenteConselhoIA`): aquele modulo usa o Event Bus local puro, que nao
atravessa a rede sozinho - achado real da vistoria de codigo de
2026-07-24 (ver docs/journal.md). Esta ponte usa ComunicacaoService de
verdade.

**O handler volta na hora** (2026-11-10, achado A1 da auditoria). O Event
Bus despacha um evento por vez; esperar a IA (ate 20 s) DENTRO do handler
prendia todo o resto do Notebook - visao, heartbeat, "a pessoa voltou" no
FOLLOW. O handler so valida e dispara o trabalho com `tarefas.disparar`.
"""
from __future__ import annotations

import logging

from orion.communication.protocol import Mensagem
from orion.communication.service import ComunicacaoService
from orion.kernel import tarefas
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
        # Fora do despacho do bus (A1): a IA pensa em segundo plano.
        tarefas.disparar(self._atender(mensagem_original), "ponte_conselho.atender")

    async def _atender(self, mensagem_original: Mensagem) -> None:
        payload = mensagem_original.payload
        try:
            conselho = await self._conselheiro.aconselhar(
                payload.get("contexto", ""),
                payload.get("opcoes") or [],
                seguranca_ativa=payload.get("seguranca_ativa", False),
            )
        except Exception:
            # Falha do conselheiro nao pode derrubar o Notebook nem deixar
            # o Pi esperando sem resposta - responde "sem conselho" em vez de
            # deixar o timeout agir, ja que aqui (diferente do
            # AtendenteConselhoIA local) o Pi fica de fato esperando um
            # comm.request.
            logger.exception("conselheiro falhou")
            await self._servico.responder(mensagem_original, {"ok": False})
            return

        if conselho is None or not conselho.aceito:
            await self._servico.responder(mensagem_original, {"ok": False})
            return

        await self._servico.responder(
            mensagem_original,
            {
                "ok": True,
                "comportamento": conselho.comportamento,
                "motivo": conselho.motivo,
                # o numero da acao (mm ou graus) - ver Conselho.valor
                "valor": getattr(conselho, "valor", 0.0),
            },
        )
