"""Orquestrador do Vision Core (Cap 8).

Liga captura, deteccao YOLO, reconhecimento facial, rastreamento e
Pan/Tilt num unico pipeline, publicando eventos no Event Bus local do
Notebook (Cap 8 s.5). O Vision Core nunca toma decisao estrategica (Cap 8
s.6) - so publica informacao estruturada.

Recuperacao de camera desconectada (Cap 8 s.9): ao falhar, publica
vision.camera_error e tenta reabrir periodicamente, mantendo o resto do
ORION OS operacional (modo SEM_VISAO) em vez de derrubar o processo.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

import cv2

from orion.kernel.event_bus import EventBus, Prioridade
from orion.vision.captura import CapturaCamera, ErroCamera
from orion.vision.deteccao import Deteccao, DetectorYolo
from orion.vision.pan_tilt import CalculadoraPanTilt, LimitesPanTilt
from orion.vision.rastreamento import Caixa, Rastreador
from orion.vision.reconhecimento_facial import ReconhecedorFacial, RostoReconhecido

logger = logging.getLogger("orion.vision.vision_core")

CallbackPanTilt = Callable[[float, float], Awaitable[None]]


class VisionCore:
    def __init__(
        self,
        event_bus: EventBus,
        indice_camera: int = 0,
        camera_espelhada: bool = False,
        confianca_minima: float = 0.55,
        limites_pan_tilt: LimitesPanTilt | None = None,
        publicar_pan_tilt: CallbackPanTilt | None = None,
        captura: CapturaCamera | None = None,
        detector: DetectorYolo | None = None,
        reconhecedor: ReconhecedorFacial | None = None,
        timeout_alvo_perdido_s: float = 2.0,
    ) -> None:
        # captura/detector/reconhecedor sao injetaveis para testar a logica
        # de orquestracao (associacao de identidade, eventos, rastreamento)
        # sem precisar de camera/modelos de verdade.
        self._event_bus = event_bus
        self._captura = captura or CapturaCamera(indice=indice_camera, espelhado=camera_espelhada)
        self._detector = detector or DetectorYolo(confianca_minima=confianca_minima)
        self._reconhecedor = reconhecedor or ReconhecedorFacial()
        self._rastreador = Rastreador(timeout_perdido_s=timeout_alvo_perdido_s)
        self._pan_tilt = CalculadoraPanTilt(limites_pan_tilt)
        self._publicar_pan_tilt = publicar_pan_tilt
        self._executando = False
        self._tinha_alvo = False
        self._ultimo_processamento_ts: float | None = None

    def carregar_pessoas_conhecidas(self, pessoas: list[dict]) -> None:
        self._reconhecedor.carregar_pessoas_conhecidas(pessoas)

    async def iniciar_camera(self) -> None:
        try:
            await self._captura.abrir()
        except ErroCamera as erro:
            await self._event_bus.publish(
                "vision.camera_error", {"motivo": str(erro)}, prioridade=Prioridade.ALTA
            )
            raise

    async def processar_um_frame(self):
        """Uma iteracao do pipeline (Cap 8 s.3). Retorna o frame lido (BGR,
        formato OpenCV) - util para quem quiser exibir/depurar."""
        agora = time.monotonic()
        intervalo_s = (
            agora - self._ultimo_processamento_ts if self._ultimo_processamento_ts else 0.1
        )
        self._ultimo_processamento_ts = agora

        try:
            frame = await self._captura.ler_frame()
        except ErroCamera as erro:
            await self._event_bus.publish(
                "vision.camera_error", {"motivo": str(erro)}, prioridade=Prioridade.ALTA
            )
            raise

        deteccoes = await self._detector.detectar(frame)
        pessoas_detectadas = DetectorYolo.pessoas(deteccoes)

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rostos = await self._reconhecedor.reconhecer(frame_rgb)

        for deteccao in deteccoes:
            topico = "vision.person_detected" if deteccao.classe == "person" else "vision.object_detected"
            await self._event_bus.publish(
                topico,
                {
                    "classe": deteccao.classe,
                    "confianca": deteccao.confianca,
                    "caixa": deteccao.caixa,
                    "timestamp": agora,
                },
            )

        identidades = self._associar_identidades(pessoas_detectadas, rostos)
        caixas = [d.caixa for d in pessoas_detectadas]
        alvo = self._rastreador.atualizar(caixas, identidades, agora=agora)

        if alvo is not None:
            self._tinha_alvo = True
            if alvo.pessoa_id is not None:
                await self._event_bus.publish(
                    "vision.person_recognized",
                    {"pessoa_id": alvo.pessoa_id, "nome": alvo.nome, "timestamp": agora},
                )

            altura, largura = frame.shape[:2]
            cx, cy = self._centro_caixa(alvo.caixa)
            pan, tilt = self._pan_tilt.calcular_correcao(cx, cy, largura, altura, intervalo_s)
            # publica sempre (mesmo sem callback de hardware) - o avatar
            # (Cap 13) reflete a direcao calculada independente do robo
            # fisico estar montado ou nao.
            await self._event_bus.publish("motion.pan_tilt", {"pan": pan, "tilt": tilt})
            if self._publicar_pan_tilt is not None:
                await self._publicar_pan_tilt(pan, tilt)

            if self._pan_tilt.alvo_centralizado(cx, cy, largura, altura):
                await self._event_bus.publish(
                    "vision.target_centered", {"pan": pan, "tilt": tilt, "timestamp": agora}
                )
        elif self._tinha_alvo:
            await self._event_bus.publish("vision.person_lost", {"timestamp": agora})
            self._tinha_alvo = False

        return frame

    async def executar(self, intervalo_reconexao_s: float = 3.0) -> None:
        """Loop continuo ate parar() ser chamado. Reconecta automaticamente
        se a camera cair (Cap 8 s.9)."""
        self._executando = True
        while self._executando:
            if not self._captura.aberta:
                try:
                    await self.iniciar_camera()
                except ErroCamera:
                    await asyncio.sleep(intervalo_reconexao_s)
                    continue
            try:
                await self.processar_um_frame()
            except ErroCamera:
                await self._captura.fechar()
                await asyncio.sleep(intervalo_reconexao_s)

    def parar(self) -> None:
        self._executando = False

    @staticmethod
    def _centro_caixa(caixa: Caixa) -> tuple[float, float]:
        x1, y1, x2, y2 = caixa
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @staticmethod
    def _associar_identidades(
        pessoas_detectadas: list[Deteccao], rostos: list[RostoReconhecido]
    ) -> dict[Caixa, tuple[int | None, str | None]]:
        """Casa cada caixa de pessoa (YOLO) com o rosto (face_recognition)
        de maior sobreposicao - vem de dois modelos diferentes, cada um
        com sua propria caixa."""
        identidades: dict[Caixa, tuple[int | None, str | None]] = {}
        for pessoa in pessoas_detectadas:
            melhor: RostoReconhecido | None = None
            melhor_sobreposicao = 0.0
            for rosto in rostos:
                sobreposicao = VisionCore._sobreposicao(pessoa.caixa, rosto.caixa)
                if sobreposicao > melhor_sobreposicao:
                    melhor_sobreposicao = sobreposicao
                    melhor = rosto
            if melhor is not None and melhor_sobreposicao > 0:
                identidades[pessoa.caixa] = (melhor.pessoa_id, melhor.nome)
        return identidades

    @staticmethod
    def _sobreposicao(caixa_a: Caixa, caixa_b: Caixa) -> float:
        ax1, ay1, ax2, ay2 = caixa_a
        bx1, by1, bx2, by2 = caixa_b
        largura_i = max(0, min(ax2, bx2) - max(ax1, bx1))
        altura_i = max(0, min(ay2, by2) - max(ay1, by1))
        return float(largura_i * altura_i)
