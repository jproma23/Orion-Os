"""Ponte entre a API de memoria e o Communication Core (Cap 14 s.7; Cap 11 s.6).

Expoe memory.remember/recall/update/forget/context/stats ao Notebook via
comm.request: um COMMAND com payload {"comando": "memory.<operacao>", ...}
recebido pelo ComunicacaoService (que ja ACKa e publica
`comm.mensagem.command` no Event Bus) vira uma chamada na MemoryAPI,
respondida com uma RESPONSE (Cap 5 s.6).

Limitacao conhecida: ainda nao valida a origem da solicitacao contra o
Service Registry (Cap 14 s.9 / Cap 11 s.8 exigem isso) - o Communication
Core em si nao implementa esse controle de acesso ainda. Revisitar quando a
Fase 2 ganhar autenticacao de origem.
"""
from __future__ import annotations

import logging
from typing import Any

from motion_core.memory.api import ErroCategoriaInvalida, ErroColunaInvalida, MemoryAPI
from orion.communication.protocol import Mensagem
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("motion_core.memory.bridge")

PREFIXO_COMANDO = "memory."


class PonteMemoria:
    """Liga comandos `memory.*` recebidos via comm.request a MemoryAPI."""

    def __init__(self, memory_api: MemoryAPI, servico: ComunicacaoService) -> None:
        self._memory_api = memory_api
        self._servico = servico

    def registrar(self, event_bus: EventBus) -> None:
        event_bus.subscribe("comm.mensagem.command", self._ao_receber_comando)

    async def _ao_receber_comando(self, evento: Evento) -> None:
        comando = evento.dados.get("payload", {}).get("comando", "")
        if not comando.startswith(PREFIXO_COMANDO):
            return  # nao e um comando de memoria - outro modulo cuida disso

        operacao = comando[len(PREFIXO_COMANDO) :]
        mensagem_original = Mensagem.from_dict(evento.dados)

        try:
            resultado = await self._executar(operacao, mensagem_original.payload)
        except (ErroCategoriaInvalida, ErroColunaInvalida, KeyError, TypeError) as erro:
            logger.warning("Comando de memoria invalido ('%s'): %s", comando, erro)
            await self._servico.responder(mensagem_original, {"ok": False, "erro": str(erro)})
            return

        await self._servico.responder(mensagem_original, {"ok": True, "resultado": resultado})

    async def _executar(self, operacao: str, payload: dict[str, Any]) -> Any:
        if operacao == "remember":
            return await self._memory_api.remember(payload["categoria"], payload["dados"])
        if operacao == "recall":
            return await self._memory_api.recall(
                payload["categoria"], payload.get("filtro"), payload.get("limite", 20)
            )
        if operacao == "update":
            return await self._memory_api.update(
                payload["categoria"], payload["id"], payload["dados"]
            )
        if operacao == "forget":
            return await self._memory_api.forget(payload["categoria"], payload["id"])
        if operacao == "context":
            return await self._memory_api.context(
                payload.get("pessoa_id"), payload.get("limite_conversas", 10)
            )
        if operacao == "stats":
            return await self._memory_api.stats()
        raise KeyError(f"Operacao de memoria desconhecida: '{operacao}'")
