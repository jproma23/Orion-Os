"""Painel dos sentidos: o robo sabe quando esta cego, surdo ou sem corpo.

Pedido do dono (2026-11-15), item 1 do plano de autonomia: "autodiagnostico
que conversa". O caso que o motivou: a DV20 parou de mandar quadros em 14/11
17:40 e por 13 h nenhuma parte do robo soube - ele mapeou, andou, bateu no
corredor e disse "ja estou atras de voce" com a camera cega. Cada sentido ja
sabia dizer o proprio estado (`motivo_cego` da visao, `conectado` dos
ouvidos, o supervisor do link); faltava alguem PERGUNTAR, guardar DESDE
QUANDO, e entregar isso a quem conversa.

Cada sentido e um callable sem argumento: None = estou bem, texto = o motivo
da falha. O painel nao conhece os sentidos por dentro - so pergunta.

Falha so vale depois de `confirmar_s` seguidos: a visao fica "sem cena nova"
por um instante a cada reabertura, e um robo que anuncia cegueira a cada
piscada perde a credibilidade do aviso que importa.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

logger = logging.getLogger("orion.mission.sentidos")

Verificador = Callable[[], "str | None"]

#: Quanto tempo seguido falhando ate virar fato (s).
CONFIRMAR_S = 10.0
#: De quanto em quanto tempo o laco de fundo pergunta (s).
INTERVALO_S = 5.0


@dataclass
class _Sentido:
    nome: str
    verificar: Verificador
    #: monotonic de quando comecou a falhar (ainda nao confirmado ou ja).
    falhando_desde: float | None = None
    #: hora local de quando a falha COMECOU - e o que a fala diz ("desde 17:40").
    falhando_desde_hora: datetime | None = None
    confirmado: bool = False
    motivo: str = ""


class PainelSentidos:
    def __init__(
        self,
        confirmar_s: float = CONFIRMAR_S,
        relogio: Callable[[], float] = time.monotonic,
        agora: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._confirmar_s = confirmar_s
        self._relogio = relogio
        self._agora = agora
        self._sentidos: list[_Sentido] = []

    def registrar(self, nome: str, verificar: Verificador) -> None:
        self._sentidos.append(_Sentido(nome, verificar))

    def avaliar(self) -> None:
        """Pergunta a cada sentido e registra as bordas (caiu / voltou)."""
        agora = self._relogio()
        for s in self._sentidos:
            try:
                motivo = s.verificar()
            except Exception as erro:  # noqa: BLE001 - verificar quebrado = sentido sem resposta
                motivo = f"nao consegui verificar ({erro})"
            if not motivo:
                if s.confirmado:
                    logger.info("sentido VOLTOU: %s", s.nome)
                s.falhando_desde = None
                s.falhando_desde_hora = None
                s.confirmado = False
                s.motivo = ""
                continue
            s.motivo = motivo
            if s.falhando_desde is None:
                s.falhando_desde = agora
                s.falhando_desde_hora = self._agora()
            if not s.confirmado and agora - s.falhando_desde >= self._confirmar_s:
                s.confirmado = True
                logger.error(
                    "sentido CAIU: %s - %s (desde %s)",
                    s.nome, motivo, s.falhando_desde_hora.strftime("%H:%M"),
                )

    def falhas(self) -> list[tuple[str, str, str]]:
        """(nome, motivo, "HH:MM" de quando comecou) dos sentidos confirmados."""
        return [
            (s.nome, s.motivo, s.falhando_desde_hora.strftime("%H:%M"))
            for s in self._sentidos
            if s.confirmado and s.falhando_desde_hora is not None
        ]

    def bloco(self) -> str:
        """O texto que entra no prompt. Vazio se nada foi registrado."""
        if not self._sentidos:
            return ""
        falhas = self.falhas()
        if not falhas:
            nomes = ", ".join(s.nome for s in self._sentidos)
            return f"MEUS SENTIDOS AGORA: todos respondendo ({nomes})."
        linhas = [
            f"- {nome}: FALHANDO desde as {desde} - {motivo}" for nome, motivo, desde in falhas
        ]
        return (
            "MEUS SENTIDOS AGORA (autodiagnostico):\n"
            + "\n".join(linhas)
            + "\n- o que depende de um sentido que falha, eu NAO sei agora: diga isso, "
            "e se perguntarem como estou, conte o que esta falhando e desde quando"
        )

    async def vigiar(self, intervalo_s: float = INTERVALO_S) -> None:
        """Laco de fundo: sem ele, "desde" seria a hora da primeira pergunta."""
        while True:
            self.avaliar()
            await asyncio.sleep(intervalo_s)
