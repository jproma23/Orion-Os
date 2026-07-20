"""Sentinela de visão (Cap 8; EDR-0020 Modo Sentinela): detecta ROSTO
DESCONHECIDO na câmera e dispara o alerta que o maestro (Vigília) trata.

Carrega os rostos conhecidos (a família, via memory.recall) e, de tempos
em tempos, olha um frame da câmera: se aparece um rosto que NÃO casa com
nenhum conhecido, é um "estranho" -> salva a foto e publica
`sentinela.alerta {tipo: "pessoa"}` (encaminhado ao Pi, onde a Vigília
assume). Roda no Notebook (a visão é do Mission Core, Cap 8).

Roda devagar de propósito (intervalo de alguns segundos): face_recognition
na CPU é pesado; não precisamos de tempo real para vigiar a sala. Um
cooldown evita repetir o alerta a cada frame enquanto o estranho continua
no quadro.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

from orion.kernel.event_bus import EventBus, Prioridade
from orion.vision.captura import CapturaCamera

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

logger = logging.getLogger("orion.vision.sentinela_visao")


class SentinelaVisao:
    def __init__(
        self,
        event_bus: EventBus,
        reconhecedor,
        captura: CapturaCamera,
        carregar_conhecidos: Callable[[], Awaitable[list[dict]]],
        intervalo_s: float,
        cooldown_s: float,
        pasta_fotos: str,
    ) -> None:
        self._event_bus = event_bus
        self._reconhecedor = reconhecedor
        self._captura = captura
        self._carregar_conhecidos = carregar_conhecidos
        self._intervalo_s = intervalo_s
        self._cooldown_s = cooldown_s
        self._pasta_fotos = Path(pasta_fotos)
        self._ultimo_alerta = 0.0
        self._pausado = False

    async def _carregar_familia(self) -> bool:
        """Carrega os rostos conhecidos (com algumas tentativas, pois o link
        com o Pi pode ainda estar subindo). Retorna True se carregou alguém -
        sem rostos conhecidos, TODO mundo seria estranho, então desligamos."""
        for tentativa in range(5):
            try:
                pessoas = await self._carregar_conhecidos()
            except Exception:
                logger.debug("carga de rostos falhou (tentativa %d)", tentativa + 1, exc_info=True)
                await asyncio.sleep(3)
                continue
            conhecidos = [p for p in (pessoas or []) if p.get("embedding_face")]
            if conhecidos:
                self._reconhecedor.carregar_pessoas_conhecidas(conhecidos)
                logger.info("Sentinela: %d rosto(s) conhecido(s) carregado(s)", len(conhecidos))
                return True
            await asyncio.sleep(3)
        return False

    def pausar(self) -> None:
        """Suspende a vigilancia sem derrubar o laco (ver retomar).

        Usado pelo alivio de carga: reconhecimento facial e a parte mais
        pesada do Notebook, e enquanto a RAM esta critica e melhor ficar
        cego por alguns minutos do que travar a maquina inteira.
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

    async def executar(self) -> None:
        self._pasta_fotos.mkdir(parents=True, exist_ok=True)
        if not await self._carregar_familia():
            logger.warning("Sentinela de visão desativada: nenhum rosto conhecido no banco")
            return
        try:
            await self._captura.abrir()
        except Exception:
            logger.warning("Sentinela de visão desativada: câmera indisponível", exc_info=True)
            return
        logger.info("Sentinela de visão ativa - vigiando rostos desconhecidos")
        try:
            while True:
                if not self._pausado:
                    await self._checar_uma_vez()
                await asyncio.sleep(self._intervalo_s)
        finally:
            await self._captura.fechar()

    async def _checar_uma_vez(self) -> None:
        try:
            frame_bgr = await self._captura.ler_frame()
        except Exception:
            logger.debug("falha ao ler frame da câmera", exc_info=True)
            return
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB) if cv2 else frame_bgr
        rostos = await self._reconhecedor.reconhecer(frame_rgb)
        estranhos = [r for r in rostos if r.pessoa_id is None]
        conhecidos = [r.nome for r in rostos if r.pessoa_id is not None]
        if conhecidos:
            logger.debug("reconhecidos: %s", ", ".join(conhecidos))
        if not estranhos:
            return

        agora = time.monotonic()
        if agora - self._ultimo_alerta < self._cooldown_s:
            return  # ainda no cooldown - não repete o alerta
        self._ultimo_alerta = agora

        caminho = self._salvar_foto(frame_bgr)
        logger.warning("SENTINELA: %d rosto(s) desconhecido(s) - alerta!", len(estranhos))
        await self._event_bus.publish(
            "sentinela.alerta",
            {"tipo": "pessoa", "desconhecidos": len(estranhos), "foto": caminho},
            prioridade=Prioridade.ALTA,
        )

    def _salvar_foto(self, frame_bgr) -> str:
        nome = f"estranho_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        caminho = self._pasta_fotos / nome
        if cv2 is not None:
            cv2.imwrite(str(caminho), frame_bgr)
        return str(caminho)
