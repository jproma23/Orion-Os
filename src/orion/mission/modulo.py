"""Modulo de missao/IA do Mission Core (EDR-0023).

Reune o que estava espalhado dentro de tools/conversar_fofao.py: o AiManager
(IA remota com fallback pro Ollama, EDR-0021), o MissionPlanner (Cap 7) e as
duas pontes que atendem pedidos vindos do Pi - PonteDecisao (EDR-0022, a IA
estrategica) e PonteConselho (o Mentor).

Depende do ComunicacaoService porque o planner precisa mandar comando ao
Hardware Core e consultar a memoria, e porque as duas pontes atendem pedidos
que chegam pelo enlace com o Pi. Continua sem conhecer outros modulos: tudo
que sai daqui sai pelo Event Bus (regra 1 do ARQUITETURA.txt).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from orion.kernel.event_bus import EventBus

logger = logging.getLogger("orion.mission.modulo")


class ModuloMissao:
    nome = "mission"

    def __init__(
        self,
        event_bus: EventBus,
        comm: Any,
        conf_ia: dict[str, Any],
        ia: Any | None = None,
    ) -> None:
        # `ia` injetavel: os testes trocam por um dublê e nao precisam de
        # chave de API nem do Ollama instalado.
        self._event_bus = event_bus
        self._comm = comm
        self._conf = conf_ia
        self._ia = ia
        self._planner: Any | None = None
        self._pronto = False

    async def iniciar(self) -> None:
        from orion.mission.decisao_estrategica import PonteDecisao
        from orion.mission.memory_client import MemoryClient
        from orion.mission.mission_planner import MissionPlanner

        if self._ia is None:
            self._ia = self._construir_ia()

        self._planner = MissionPlanner(
            self._ia,
            enviar_comando_hardware=self._enviar_comando_hardware,
            memory_client=MemoryClient(self._comm),
        )

        # A IA estrategica do maestro (EDR-0022) manda "mission.decidir" pelo
        # enlace com o Pi; sem esta ponte registrada o pedido chega e ninguem
        # responde - o comportamento fica mudo sem dar erro.
        PonteDecisao(self._ia, self._comm).registrar(self._event_bus)

        self._registrar_mentor()
        self._pronto = True
        logger.info("Mission Core ativo (provider=%s)", self._conf.get("provider", "ollama"))

    def _construir_ia(self) -> Any:
        from orion.mission.ai_manager import AiManager

        provider = self._conf.get("provider", "ollama")
        if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
            # Nao e erro fatal: o AiManager cai sozinho pro Ollama local
            # (EDR-0021). Mas avisar importa - senao a conta de "por que a
            # resposta ficou ruim" so aparece muito depois.
            logger.warning(
                "provider=openai sem OPENAI_API_KEY no ambiente - toda resposta "
                "vai cair no Ollama local (EDR-0021)"
            )
        return AiManager(
            modelo=self._conf["ollama_model"],
            temperatura=self._conf["temperature"],
            caminho_prompt_sistema=self._conf["system_prompt_file"],
            max_tokens_resposta=self._conf["resposta_max_tokens"],
            keep_alive_minutes=self._conf["keep_alive_minutes"],
            provider=provider,
            openai_model=self._conf.get("openai_model", "gpt-4o-mini"),
            openai_base_url=self._conf.get("openai_base_url"),
        )

    def _registrar_mentor(self) -> None:
        """Mentor de comportamento: tolerado ausente (Cap 6 s.8).

        Sem OPENAI_API_KEY ou sem a biblioteca `openai`, o Mentor simplesmente
        nunca tem conselho a dar - nao pode derrubar o boot por isso.
        """
        try:
            from orion.mission.conselheiro_comportamento import ConselheiroComportamento
            from orion.mission.ponte_conselho import PonteConselho

            conselheiro = ConselheiroComportamento(
                modelo=self._conf.get("mentor_model", "openai/gpt-4o-mini"),
                base_url=self._conf.get("openai_base_url"),
            )
            PonteConselho(conselheiro, self._comm).registrar(self._event_bus)
            logger.info("Mentor de comportamento ativo (IA remota)")
        except Exception:  # noqa: BLE001
            logger.warning("Mentor de comportamento indisponivel - seguindo sem ele", exc_info=True)

    async def _enviar_comando_hardware(self, comando: str) -> None:
        """Manda um COMMAND ao Mega pela cadeia TCP -> serial e espera o ACK.

        O Notebook nunca fala com o Arduino direto (regra 2 do ARQUITETURA.txt): o
        comando vai para "hardware_core" e o Raspberry o repassa pela serial.
        """
        await self._comm.send("hardware_core", {"comando": comando})
        logger.info("Comando '%s' entregue ao Hardware Core (ACK recebido)", comando)

    async def processar(self, texto: str) -> str:
        """Entrada de texto -> resposta. Usada pela voz e pelo chat da web."""
        if self._planner is None:
            raise RuntimeError("ModuloMissao.iniciar() ainda nao foi chamado")
        return await self._planner.processar(texto)

    async def encerrar(self) -> None:
        self._pronto = False
        self._planner = None

    def esta_saudavel(self) -> bool:
        return self._pronto
