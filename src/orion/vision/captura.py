"""Captura de frames da camera (Cap 8 secao 2-3).

cv2.VideoCapture e bloqueante (abrir e ler frame) - cada chamada roda em
thread separada via `asyncio.to_thread`, para nao travar o Event Bus nem
os demais modulos do Mission Core.

A camera integrada do notebook ("PC Camera") fica em indice local (0/1).
A webcam dedicada a visao computacional ("DV20 USB", no pan/tilt) so
alcanca fisicamente o Raspberry Pi - o Pi a reexpoe na rede via
`orion-camera-stream.service` (MJPEG/HTTP), e aqui ela e aberta por URL
em vez de indice; `cv2.VideoCapture` aceita os dois formatos igual.
`abrir()` descarta os primeiros
frames de propósito - na pratica, o primeiro frame lido logo apos abrir a
porta costuma vir quase preto (a camera ainda ajustando exposicao/balanco
de branco), confirmado com a webcam real desta montagem.

A webcam frontal desta montagem entrega o frame ja espelhado (efeito
"selfie"), o que inverte a direcao real de quem se move na cena - sem
corrigir isso, o Pan/Tilt (Cap 8 s.8) gira para o lado errado ao seguir
uma pessoa. `espelhado=True` desfaz esse espelhamento logo na captura,
antes de YOLO/reconhecimento/rastreamento, para que todo o resto do
pipeline trabalhe com a orientacao real do mundo.

QUADRO ATRASADO EM FONTE DE REDE (2026-07-28): num stream MJPEG/HTTP o
produtor nao espera o consumidor. O ustreamer no Pi manda quadro continuamente
e o `cv2.VideoCapture` le a fila EM ORDEM - entao, se o Vision Core processa
mais devagar que o Pi produz (YOLO + Whisper num Notebook com carga ~3), a
fila do socket acumula e cada `read()` devolve o quadro MAIS VELHO da fila.
Medido nesta bancada: 280KB parados na Send-Q, ~2s de atraso permanente.

O efeito nao e "video travado", e coisa pior: o Pan/Tilt vira um controle
proporcional com ATRASO MORTO grande, e passa do ponto sempre. Medido com a
pessoa PARADA, o erro horizontal fazia +202 -> 0 -> -156 -> 0 -> +197, varrendo
de ponta a ponta sem nunca assentar. O sintoma relatado foi "fica maluco indo
pra outra direcao" - a direcao estava certa, faltava chegar a informacao nova.

Por isso `descartar_atrasados` pula o que ja envelheceu e usa o quadro mais
recente. Ligado por padrao QUANDO A FONTE E URL (str) e desligado para indice
local, onde o driver ja entrega o quadro corrente e a fila nao existe.
"""
from __future__ import annotations

import asyncio
import logging
import time

import cv2
import numpy as np

logger = logging.getLogger("orion.vision.captura")

#: Teto de quadros descartados por leitura. So existe para o laco nao virar
#: infinito se a fonte despejar mais rapido do que da para drenar.
MAXIMO_DESCARTES = 30

#: Um `grab()` que volta praticamente na hora veio da FILA (quadro velho). Um
#: que demora esperou o proximo quadro nascer - sinal de que chegamos na ponta
#: viva do stream e devemos parar de descartar. 10ms separa os dois casos com
#: folga: a 6fps o intervalo real entre quadros e ~167ms.
LIMIAR_QUADRO_DA_FILA_S = 0.010


class ErroCamera(Exception):
    """Falha ao abrir ou ler da camera (Cap 8 secao 9)."""


class CapturaCamera:
    def __init__(
        self,
        indice: int | str = 0,
        largura: int = 640,
        altura: int = 480,
        frames_aquecimento: int = 15,
        espelhado: bool = False,
        descartar_atrasados: bool | None = None,
    ) -> None:
        self._indice = indice
        self._largura = largura
        self._altura = altura
        self._frames_aquecimento = frames_aquecimento
        self._espelhado = espelhado
        # None = decide pela fonte: URL (str) acumula fila, indice local nao.
        self._descartar_atrasados = (
            isinstance(indice, str) if descartar_atrasados is None else descartar_atrasados
        )
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
            # Pede buffer minimo. Nem todo backend obedece (o FFMPEG, usado nas
            # URLs, costuma ignorar) - por isso o descarte no ler_frame existe
            # de qualquer forma, e nao confiamos so nisto.
            captura.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            for _ in range(self._frames_aquecimento):
                captura.read()  # deixa a exposicao/balanco de branco assentar
            return captura

        self._captura = await asyncio.to_thread(_abrir)
        logger.info(
            "Camera %s aberta (%dx%d)%s",
            self._indice,
            self._largura,
            self._altura,
            " - descartando quadro atrasado" if self._descartar_atrasados else "",
        )

    async def ler_frame(self) -> np.ndarray:
        if not self.aberta:
            raise ErroCamera("Camera nao esta aberta - chame abrir() primeiro")

        def _ler() -> np.ndarray:
            if self._descartar_atrasados:
                frame = self._ler_o_mais_recente()
            else:
                ok, frame = self._captura.read()
                if not ok or frame is None:
                    raise ErroCamera("Falha ao ler frame da camera (desconectada?)")
            if self._espelhado:
                frame = cv2.flip(frame, 1)  # desfaz o espelhamento "selfie" da camera
            return frame

        return await asyncio.to_thread(_ler)

    def _ler_o_mais_recente(self) -> np.ndarray:
        """Descarta o que ja estava na fila e decodifica so o quadro atual.

        `grab()` apenas retira da fila sem decodificar (barato); `retrieve()`
        decodifica o ultimo que foi retirado. Descartar com `read()` custaria
        um JPEG decodificado por quadro jogado fora.
        """
        descartados = 0
        while descartados < MAXIMO_DESCARTES:
            inicio = time.monotonic()
            if not self._captura.grab():
                raise ErroCamera("Falha ao ler frame da camera (desconectada?)")
            if time.monotonic() - inicio > LIMIAR_QUADRO_DA_FILA_S:
                break  # este esperou o quadro nascer: estamos na ponta viva
            descartados += 1

        ok, frame = self._captura.retrieve()
        if not ok or frame is None:
            raise ErroCamera("Falha ao decodificar frame da camera")
        if descartados >= MAXIMO_DESCARTES:
            # Nao e fatal, mas denuncia consumidor lento demais para a fonte -
            # o atraso volta a crescer e o Pan/Tilt volta a passar do ponto.
            logger.warning(
                "descartei %d quadros atrasados numa leitura so - o Vision Core "
                "nao esta acompanhando a taxa da camera",
                descartados,
            )
        return frame

    async def fechar(self) -> None:
        if self._captura is not None:
            await asyncio.to_thread(self._captura.release)
            self._captura = None
            logger.info("Camera %s fechada", self._indice)
