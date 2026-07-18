"""Captura de frames da camera (Cap 8 secao 2-3).

cv2.VideoCapture e bloqueante (abrir e ler frame) - cada chamada roda em
thread separada via `asyncio.to_thread`, para nao travar o Event Bus nem
os demais modulos do Mission Core.

Duas cameras fisicas confirmadas nesta montagem: a integrada do notebook
("PC Camera", indice 0/1) e a webcam USB externa dedicada a visao
computacional ("DV20 USB", indice 2/3). `abrir()` descarta os primeiros
frames de propósito - na pratica, o primeiro frame lido logo apos abrir a
porta costuma vir quase preto (a camera ainda ajustando exposicao/balanco
de branco), confirmado com a webcam real desta montagem.

A webcam frontal desta montagem entrega o frame ja espelhado (efeito
"selfie"), o que inverte a direcao real de quem se move na cena - sem
corrigir isso, o Pan/Tilt (Cap 8 s.8) gira para o lado errado ao seguir
uma pessoa. `espelhado=True` desfaz esse espelhamento logo na captura,
antes de YOLO/reconhecimento/rastreamento, para que todo o resto do
pipeline trabalhe com a orientacao real do mundo.
"""
from __future__ import annotations

import asyncio
import logging

import cv2
import numpy as np

logger = logging.getLogger("orion.vision.captura")


class ErroCamera(Exception):
    """Falha ao abrir ou ler da camera (Cap 8 secao 9)."""


class CapturaCamera:
    def __init__(
        self,
        indice: int = 0,
        largura: int = 640,
        altura: int = 480,
        frames_aquecimento: int = 15,
        espelhado: bool = False,
    ) -> None:
        self._indice = indice
        self._largura = largura
        self._altura = altura
        self._frames_aquecimento = frames_aquecimento
        self._espelhado = espelhado
        self._captura: cv2.VideoCapture | None = None

    @property
    def aberta(self) -> bool:
        return self._captura is not None and self._captura.isOpened()

    async def abrir(self) -> None:
        def _abrir() -> cv2.VideoCapture:
            captura = cv2.VideoCapture(self._indice)
            if not captura.isOpened():
                raise ErroCamera(f"Nao foi possivel abrir a camera indice {self._indice}")
            captura.set(cv2.CAP_PROP_FRAME_WIDTH, self._largura)
            captura.set(cv2.CAP_PROP_FRAME_HEIGHT, self._altura)
            for _ in range(self._frames_aquecimento):
                captura.read()  # deixa a exposicao/balanco de branco assentar
            return captura

        self._captura = await asyncio.to_thread(_abrir)
        logger.info("Camera %d aberta (%dx%d)", self._indice, self._largura, self._altura)

    async def ler_frame(self) -> np.ndarray:
        if not self.aberta:
            raise ErroCamera("Camera nao esta aberta - chame abrir() primeiro")

        def _ler() -> np.ndarray:
            ok, frame = self._captura.read()
            if not ok or frame is None:
                raise ErroCamera("Falha ao ler frame da camera (desconectada?)")
            if self._espelhado:
                frame = cv2.flip(frame, 1)  # desfaz o espelhamento "selfie" da camera
            return frame

        return await asyncio.to_thread(_ler)

    async def fechar(self) -> None:
        if self._captura is not None:
            await asyncio.to_thread(self._captura.release)
            self._captura = None
            logger.info("Camera %d fechada", self._indice)
