"""Guardião de RAM do Notebook (EDR-0020; Cap 16 Diagnóstico).

O Notebook tem RAM apertada (gemma3 ~3,6 GB) e já travou uma vez. Este
monitor roda no Raspberry (nó estável) e vigia a RAM livre do Notebook, que
chega como evento `diagnostic.notebook_health`. Quando a folga cai abaixo do
limiar crítico, ele pede para o Notebook **aliviar a carga** (evento
`behavior.reduzir_carga_ia`) e emite um alerta - agindo ANTES do crash.

Não é um Comportamento do maestro (não usa o "corpo" do robô); é um vigia de
saúde que roda em paralelo. Usa histerese: entra em alerta abaixo do limiar
crítico e só sai quando a RAM volta a uma folga confortável - evita ficar
ligando/desligando o alerta perto do limite.
"""
from __future__ import annotations

import logging

from orion.kernel.event_bus import EventBus, Evento, Prioridade

logger = logging.getLogger("motion_core.behavior.guardiao_ram")


class GuardiaoRamNotebook:
    def __init__(
        self,
        event_bus: EventBus,
        limiar_critico_mb: int,
        limiar_folga_mb: int,
    ) -> None:
        self._event_bus = event_bus
        self._limiar_critico_mb = limiar_critico_mb
        self._limiar_folga_mb = limiar_folga_mb
        self._em_alerta = False
        event_bus.subscribe("diagnostic.notebook_health", self._ao_receber_saude)

    @property
    def em_alerta(self) -> bool:
        return self._em_alerta

    async def _ao_receber_saude(self, evento: Evento) -> None:
        ram_livre = evento.dados.get("ram_livre_mb")
        if ram_livre is None:
            return

        if not self._em_alerta and ram_livre < self._limiar_critico_mb:
            self._em_alerta = True
            logger.warning(
                "RAM do Notebook critica: %d MB livres (< %d) - pedindo alivio de carga",
                ram_livre,
                self._limiar_critico_mb,
            )
            await self._event_bus.publish(
                "behavior.reduzir_carga_ia",
                {"ram_livre_mb": ram_livre, "motivo": "ram_critica"},
                prioridade=Prioridade.ALTA,
            )
            await self._event_bus.publish(
                "diagnostic.alerta",
                {"origem": "guardiao_ram", "ram_livre_mb": ram_livre},
                prioridade=Prioridade.ALTA,
            )
        elif self._em_alerta and ram_livre >= self._limiar_folga_mb:
            self._em_alerta = False
            logger.info("RAM do Notebook recuperada: %d MB livres - alerta encerrado", ram_livre)
            await self._event_bus.publish(
                "diagnostic.recuperado", {"origem": "guardiao_ram", "ram_livre_mb": ram_livre}
            )
