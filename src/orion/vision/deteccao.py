"""Deteccao de pessoas e objetos via YOLO (Cap 8 secao 3-4)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np
from ultralytics import YOLO


@dataclass
class Deteccao:
    classe: str
    confianca: float
    caixa: tuple[int, int, int, int]  # x1, y1, x2, y2, em pixels do frame


class DetectorYolo:
    """Roda a inferencia YOLO (bloqueante) numa thread separada."""

    def __init__(self, modelo: str = "yolov8n.pt", confianca_minima: float = 0.55) -> None:
        self._modelo = YOLO(modelo)
        self._confianca_minima = confianca_minima

    async def detectar(self, frame: np.ndarray) -> list[Deteccao]:
        def _inferir() -> list[Deteccao]:
            resultado = self._modelo(frame, verbose=False)[0]
            deteccoes = []
            for caixa in resultado.boxes:
                confianca = float(caixa.conf[0])
                if confianca < self._confianca_minima:
                    continue
                classe_id = int(caixa.cls[0])
                classe = self._modelo.names[classe_id]
                x1, y1, x2, y2 = (int(v) for v in caixa.xyxy[0])
                deteccoes.append(Deteccao(classe=classe, confianca=confianca, caixa=(x1, y1, x2, y2)))
            return deteccoes

        return await asyncio.to_thread(_inferir)

    @staticmethod
    def pessoas(deteccoes: list[Deteccao]) -> list[Deteccao]:
        return [d for d in deteccoes if d.classe == "person"]
