"""Sentinela de visão (Cap 8; EDR-0020 Modo Sentinela): dispara o alerta de
ROSTO DESCONHECIDO que o maestro (Vigília) trata.

Desde 2026-07-25 (EDR-0023) ela NÃO abre mais a câmera nem roda
reconhecimento facial próprio. Quem faz as duas coisas é o Vision Core, dono
do dispositivo; a Sentinela apenas ouve `vision.faces_desconhecidas` no
Event Bus e decide se aquilo vira alerta.

Duas coisas melhoraram com isso:

1. Acabou a disputa pelo `/dev/videoN`. Antes, Vision Core e Sentinela
   abriam a mesma câmera - e era por isso que o Vision Core nunca podia
   entrar em produção.
2. Acabou o reconhecimento facial DUPLICADO. Os dois rodavam
   `face_recognition` sobre os mesmos frames, e é a parte mais cara do
   Notebook (o alívio de carga existe justamente por causa dela).

O cooldown continua aqui, e não no Vision Core: é uma decisão de política de
alerta ("não repetir enquanto o estranho continua no quadro"), não uma
decisão de visão.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

from orion.kernel.event_bus import EventBus, Prioridade

logger = logging.getLogger("orion.vision.sentinela_visao")

#: recebe o caminho de destino, grava a imagem atual e devolve o caminho (ou
#: None se ainda não houve frame). Quem fornece é o dono da câmera.
SalvarFoto = Callable[[str], str | None]


class SentinelaVisao:
    def __init__(
        self,
        event_bus: EventBus,
        cooldown_s: float,
        pasta_fotos: str,
        salvar_foto: SalvarFoto | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._cooldown_s = cooldown_s
        self._pasta_fotos = Path(pasta_fotos)
        self._salvar_foto = salvar_foto
        self._ultimo_alerta = 0.0
        self._pausado = False

    def registrar(self) -> None:
        """Assina o evento do Vision Core. Substitui o antigo executar()."""
        self._pasta_fotos.mkdir(parents=True, exist_ok=True)
        self._event_bus.subscribe("vision.faces_desconhecidas", self._ao_ver_desconhecido)
        logger.info("Sentinela de visão ativa - ouvindo o Vision Core")

    def pausar(self) -> None:
        """Suspende os alertas sem desassinar o evento (ver retomar).

        Usado pelo alívio de carga. Note que agora pausar a Sentinela NÃO
        economiza CPU - quem gasta é o Vision Core, que continua rodando.
        Para aliviar de verdade é o Vision Core que precisa ser pausado.
        """
        if not self._pausado:
            self._pausado = True
            logger.warning("Sentinela de visão PAUSADA (alívio de carga)")

    def retomar(self) -> None:
        if self._pausado:
            self._pausado = False
            logger.info("Sentinela de visão retomada")

    @property
    def pausado(self) -> bool:
        return self._pausado

    async def _ao_ver_desconhecido(self, evento) -> None:
        if self._pausado:
            return

        agora = time.monotonic()
        if agora - self._ultimo_alerta < self._cooldown_s:
            return  # ainda no cooldown - não repete enquanto o estranho fica
        self._ultimo_alerta = agora

        quantidade = evento.dados.get("quantidade", 1)
        caminho = self._pedir_foto()
        logger.warning("SENTINELA: %d rosto(s) desconhecido(s) - alerta!", quantidade)
        await self._event_bus.publish(
            "sentinela.alerta",
            {"tipo": "pessoa", "desconhecidos": quantidade, "foto": caminho},
            prioridade=Prioridade.ALTA,
        )

    def _pedir_foto(self) -> str | None:
        """Falha ao salvar a foto NÃO cancela o alerta: saber que há um
        estranho vale mais que ter a imagem dele."""
        if self._salvar_foto is None:
            return None
        nome = f"estranho_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        try:
            return self._salvar_foto(str(self._pasta_fotos / nome))
        except Exception:  # noqa: BLE001
            logger.warning("Falha ao salvar a foto do estranho", exc_info=True)
            return None
