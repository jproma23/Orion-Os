"""Comportamento: a unidade plugável do Behavior Core (EDR-0020).

Cada comportamento do Fofão (atender, vigiar, patrulhar, repousar...) é uma
subclasse desta. O maestro (BehaviorCore) escolhe, a cada instante, o
comportamento de MAIOR prioridade que "quer rodar" e o coloca no controle.
"""
from __future__ import annotations

from orion.kernel.event_bus import EventBus


class Comportamento:
    """Base de um comportamento arbitrado pelo maestro.

    `prioridade`: MAIOR número = mais importante (vence a arbitragem). Segue
    a escada do EDR-0020 (100 segurança ... 10 repouso).
    """

    nome: str = "comportamento"
    prioridade: int = 0

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus

    def quer_rodar(self) -> bool:
        """True quando este comportamento deseja o controle AGORA.

        A subclasse liga isso ao seu gatilho (ex.: ouviu "Fofão", detectou
        rosto desconhecido, chegou o horário da ronda). Quando o valor muda,
        a subclasse deve chamar `maestro.pedir_reavaliacao()` para o maestro
        reagir na hora, sem esperar o próximo tick.
        """
        return False

    async def executar(self) -> None:
        """Faz o trabalho do comportamento enquanto estiver no controle.

        Pode ser **cancelada** a qualquer momento se um comportamento de
        prioridade maior assumir (preempção). Se precisar limpar algo ao ser
        interrompida, trate `asyncio.CancelledError` e re-levante. Ao voltar
        ao controle depois, `executar` é chamada de novo — cabe à subclasse
        lembrar onde parou, se quiser retomar em vez de recomeçar.
        """
        raise NotImplementedError
