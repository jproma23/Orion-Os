"""Vision Engine do Motion Core (Pi) — EDR-0023, câmera dedicada do robô.

O Raspberry é dono da CÂMERA física do robô (a USB "DV20 USB" em /dev/video0,
servida em MJPEG pelo ustreamer na porta 8091) e roda o MESMO motor de visão
que o Notebook (ORION VISION PROTOCOL): YOLO padrão COCO, com foco em
PESSOAS e ANIMAIS — o que o robô precisa ver no chão de casa.

Diferente do Vision Core do Notebook (`src/orion/vision/`), este engine:
- não faz reconhecimento facial (o banco de rostos vive no banco e o fluxo
  de "quem é" continua no Notebook);
- não move pan/tilt (quem manda nos servos é o maestro/Behavior Core);
- não espelha avatar (o avatar é do Notebook);
- publica detecções NO BARramento do Pi, com o mesmo formato padronizado do
  ORION VISION PROTOCOL:
    {"camera", "classe", "confianca", "caixa", "centro_x", "centro_y", "timestamp"}
- tolera câmera/modelo ausente (Cap 6 s.8): sem imagem o robô segue vivo e
  publica `vision.camera_error`, igual ao Vision Core do Notebook.

A câmera é lida DO STREAM do ustreamer (http://127.0.0.1:8091/stream) e não do
device diretamente: o ustreamer é o dono físico do /dev/videoN (EDR-0023 s.3)
e o engine é um consumidor do stream — abrir o device em paralelo disputaria a
câmera e é exatamente o conflito que o EDR-0023 proíbe.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import cv2

from orion.kernel.event_bus import EventBus, Prioridade

logger = logging.getLogger("motion_core.vision.engine")

#: Identificação da câmera no ORION VISION PROTOCOL.
CAMERA_RASPBERRY_FRONT = "raspberry_front"

#: Classes COCO padrão de interesse para o robô no Pi: PESSOAS + ANIMAIS.
#: Vem de `vision.pi_classes_interesse` no orion.yaml; este é o default
#: (cobre o que o dono pediu: pessoas, vacas, cães, gatos, outros animais).
CLASSES_PESSOAS_ANIMAIS = frozenset(
    {
        "person",
        "bird",
        "cat",
        "dog",
        "horse",
        "sheep",
        "cow",
        "elephant",
        "bear",
        "zebra",
        "giraffe",
    }
)


class VisionEngine:
    """Motor de visão do Pi: lê o stream da câmera, roda YOLO e publica.

    Ciclo de vida (contrato de módulo, EDR-0023): `iniciar()`, `parar()`,
    `esta_saudavel()`. Tudo bloqueante (YOLO) roda via `asyncio.to_thread`.
    """

    def __init__(
        self,
        event_bus: EventBus,
        camera_url: str = "http://127.0.0.1:8091/stream",
        caminho_modelo: str = "data/modelos/yolov8n.pt",
        confianca_minima: float = 0.55,
        imgsz: int = 320,
        fps_alvo: float = 0.0,
        classes_interesse: frozenset[str] | None = None,
        detector: Any | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._camera_url = camera_url
        self._caminho_modelo = caminho_modelo
        self._confianca_minima = confianca_minima
        self._imgsz = imgsz
        self._fps_alvo = fps_alvo
        self._classes = set(classes_interesse or CLASSES_PESSOAS_ANIMAIS)
        # detector injetável (testes usam dublê sem modelo/câmera).
        self._detector = detector
        self._captura: cv2.VideoCapture | None = None
        self._executando = False
        self._tarefa: asyncio.Task | None = None
        self._ultimo_processamento_ts: float | None = None
        #: Estado do pipeline para o diagnóstico (bloco VISION).
        self._estado: dict[str, Any] = {
            "camera": "OFFLINE",
            "frame": "SEM_FRAME",
            "model": "NAO_CARREGADO",
            "model_load": "NAO_CARREGADO",
            "inference": "PARADA",
            "detections": 0,
            "fps": 0.0,
            "classes": sorted(self._classes),
        }

    @property
    def estado(self) -> dict[str, Any]:
        """Snapshot do pipeline para a ferramenta de diagnóstico."""
        return dict(self._estado)

    def _carregar_detector(self) -> Any:
        # import local: ultralytics é pesado e pode faltar em dev/teste.
        if self._detector is not None:
            return self._detector
        from ultralytics import YOLO

        return YOLO(self._caminho_modelo)

    async def iniciar(self) -> None:
        if not self._executando:
            self._executando = True
            self._tarefa = asyncio.create_task(self._executar())

    def parar(self) -> None:
        self._executando = False

    def esta_saudavel(self) -> bool:
        return self._tarefa is not None and not self._tarefa.done()

    async def _executar(self) -> None:
        while self._executando:
            try:
                await self._processar_uma_vez()
            except Exception:
                # Cap 6 s.8: falha de visão nunca derruba o Motion Core.
                logger.exception("Vision Engine: ciclo falhou — tentando de novo")
                await asyncio.sleep(1.0)

    async def _processar_uma_vez(self) -> None:
        agora = time.monotonic()
        if self._detector is None:
            # Carrega o YOLO uma vez, em thread (bloqueia por frações de segundo).
            self._detector = await asyncio.to_thread(self._carregar_detector)
            self._estado["model"] = "YOLOv8n"
            self._estado["model_load"] = "OK"

        if self._captura is None or not self._captura.isOpened():
            captura = await asyncio.to_thread(self._abrir_captura)
            if captura is None:
                self._estado["camera"] = "OFFLINE"
                await self._event_bus.publish(
                    "vision.camera_error",
                    {"motivo": f"stream indisponivel: {self._camera_url}"},
                    prioridade=Prioridade.ALTA,
                )
                await asyncio.sleep(3.0)
                return
            self._captura = captura
            self._estado["camera"] = "ONLINE"

        frame = await asyncio.to_thread(self._ler_frame)
        if frame is None:
            self._estado["frame"] = "SEM_FRAME"
            self._estado["camera"] = "OFFLINE"
            try:
                self._captura.release()
            except Exception:
                pass
            self._captura = None
            await asyncio.sleep(3.0)
            return
        self._estado["camera"] = "ONLINE"
        self._estado["frame"] = "OK"

        resultado = await asyncio.to_thread(self._detectar, frame)
        self._estado["inference"] = "RUNNING"
        self._estado["detections"] = len(resultado)

        intervalo_s = agora - self._ultimo_processamento_ts if self._ultimo_processamento_ts else 0.1
        self._ultimo_processamento_ts = agora
        if intervalo_s > 0:
            self._estado["fps"] = round(1.0 / intervalo_s, 1)

        await self._publicar(resultado, agora)
        await self._dormir_o_resto_do_quadro(agora)

    def _abrir_captura(self) -> cv2.VideoCapture | None:
        captura = cv2.VideoCapture(self._camera_url, cv2.CAP_FFMPEG)
        if not captura.isOpened():
            return None
        return captura

    def _ler_frame(self) -> Any | None:
        if self._captura is None:
            return None
        ret, frame = self._captura.read()
        if not ret or frame is None:
            return None
        return frame

    def _detectar(self, frame) -> list[dict[str, Any]]:
        """Roda YOLO e devolve as detecções já filtradas (classes + confiança)."""
        if self._detector is None:
            return []
        r = self._detector.predict(
            frame, imgsz=self._imgsz, conf=self._confianca_minima, verbose=False
        )[0]
        nomes = r.names
        resultado: list[dict[str, Any]] = []
        for caixa, confianca, classe_id in zip(r.boxes.xyxy, r.boxes.conf, r.boxes.cls):
            nome = nomes.get(int(classe_id), "?")
            if nome not in self._classes:
                continue
            x1, y1, x2, y2 = (float(v) for v in caixa)
            resultado.append(
                {
                    "classe": nome,
                    "confianca": round(float(confianca), 2),
                    "caixa": (x1, y1, x2, y2),
                }
            )
        return resultado

    async def _publicar(self, deteccoes: list[dict[str, Any]], agora: float) -> None:
        altura, largura = 480, 640  # preenchido abaixo quando há caixa
        for d in deteccoes:
            x1, y1, x2, y2 = d["caixa"]
            topico = "vision.person_detected" if d["classe"] == "person" else "vision.object_detected"
            await self._event_bus.publish(
                topico,
                {
                    "camera": CAMERA_RASPBERRY_FRONT,
                    "classe": d["classe"],
                    "confianca": d["confianca"],
                    "caixa": [x1, y1, x2, y2],
                    # centro normalizado 0..1 (mesmo padrão de vision.person_recognized)
                    "centro_x": round(((x1 + x2) / 2) / largura, 3),
                    "centro_y": round(((y1 + y2) / 2) / altura, 3),
                    "timestamp": agora,
                },
            )

    async def _dormir_o_resto_do_quadro(self, inicio: float) -> None:
        if not self._fps_alvo or self._fps_alvo <= 0:
            return
        intervalo = 1.0 / self._fps_alvo
        sobra = intervalo - (time.monotonic() - inicio)
        await asyncio.sleep(sobra if sobra > 0 else 0)