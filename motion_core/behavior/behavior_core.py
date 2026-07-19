"""BehaviorCore: o maestro (EDR-0020) - arbitragem por prioridade.

Roda no Raspberry (Motion Core). A cada reavaliação, o comportamento de
MAIOR prioridade que "quer rodar" assume o controle; um mais forte preempta
(cancela a tarefa) o mais fraco, que pode voltar depois. É isto que faz o
Fofão parecer "vivo": ninguém manda o próximo passo, o maestro decide
sozinho a partir do que cada comportamento deseja.
"""
from __future__ import annotations

import asyncio
import logging

from motion_core.behavior.comportamento import Comportamento
from orion.kernel.event_bus import EventBus

logger = logging.getLogger("motion_core.behavior.behavior_core")

#: tick de segurança: mesmo sem gatilho, o maestro reavalia neste intervalo
#: (pega comportamentos que passaram a querer rodar sem avisar).
INTERVALO_TICK_S = 0.2


class BehaviorCore:
    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._comportamentos: list[Comportamento] = []
        self._ativo: Comportamento | None = None
        self._tarefa_ativa: asyncio.Task | None = None
        self._executando = False
        self._acordar = asyncio.Event()

    def registrar(self, comportamento: Comportamento) -> None:
        """Adiciona um comportamento e reordena do mais forte ao mais fraco."""
        comportamento._maestro = self
        self._comportamentos.append(comportamento)
        self._comportamentos.sort(key=lambda c: c.prioridade, reverse=True)

    @property
    def ativo_nome(self) -> str | None:
        """Nome do comportamento no controle agora (None = nenhum)."""
        return self._ativo.nome if self._ativo is not None else None

    def pedir_reavaliacao(self) -> None:
        """Acorda o maestro para reavaliar quem deve estar no controle.
        Um comportamento chama isto quando seu gatilho muda."""
        self._acordar.set()

    def _escolher(self) -> Comportamento | None:
        # lista já ordenada por prioridade decrescente: o primeiro que quer
        # rodar é o vencedor.
        for comportamento in self._comportamentos:
            if comportamento.quer_rodar():
                return comportamento
        return None

    async def _trocar_para(self, novo: Comportamento | None) -> None:
        if self._tarefa_ativa is not None and not self._tarefa_ativa.done():
            self._tarefa_ativa.cancel()
            try:
                await self._tarefa_ativa
            except asyncio.CancelledError:
                pass
        self._ativo = novo
        self._tarefa_ativa = None
        if novo is not None:
            logger.info("maestro: '%s' assume (prio %d)", novo.nome, novo.prioridade)
            self._tarefa_ativa = asyncio.create_task(self._rodar(novo))

    async def _rodar(self, comportamento: Comportamento) -> None:
        try:
            await comportamento.executar()
        except asyncio.CancelledError:
            logger.info("maestro: '%s' preemptado", comportamento.nome)
            raise
        except Exception:
            logger.exception("maestro: erro em '%s' (isolado)", comportamento.nome)
        finally:
            # terminou sozinho -> reavaliar quem entra agora
            self.pedir_reavaliacao()

    async def executar(self) -> None:
        """Laço principal do maestro. Roda até `parar()`."""
        self._executando = True
        while self._executando:
            # tarefa que acabou sozinha deixa de ser a ativa
            if self._tarefa_ativa is not None and self._tarefa_ativa.done():
                self._ativo = None
                self._tarefa_ativa = None

            escolhido = self._escolher()
            if escolhido is not self._ativo:
                # escolhido é sempre o de maior prioridade que quer rodar;
                # se difere do ativo, ele é o dono legítimo do controle.
                await self._trocar_para(escolhido)

            self._acordar.clear()
            try:
                await asyncio.wait_for(self._acordar.wait(), timeout=INTERVALO_TICK_S)
            except asyncio.TimeoutError:
                pass

        await self._trocar_para(None)

    def parar(self) -> None:
        self._executando = False
        self._acordar.set()
