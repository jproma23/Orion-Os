"""Captura UM frame estavel da webcam e salva. Roda no Notebook (onde a
camera esta, /dev/video0). Chamado uma vez por posicao do pan pela panoramica.

Uso: python3 capturar_frame.py <caminho_saida.jpg> [indice_camera]

Descarta os primeiros quadros de proposito: a webcam entrega os primeiros
escuros/borrados enquanto o auto-exposicao ainda nao estabilizou.
"""
from __future__ import annotations

import sys

import cv2

QUADROS_DESCARTADOS = 8  # deixa o auto-exposure/foco assentar


def main() -> int:
    saida = sys.argv[1]
    indice = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    cam = cv2.VideoCapture(indice, cv2.CAP_V4L2)
    if not cam.isOpened():
        print(f"ERRO: nao abriu a camera {indice}", file=sys.stderr)
        return 1
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    frame = None
    for _ in range(QUADROS_DESCARTADOS):
        ok, frame = cam.read()
    cam.release()

    if frame is None:
        print("ERRO: nao capturou quadro", file=sys.stderr)
        return 1
    cv2.imwrite(saida, frame)
    print(f"ok {saida} {frame.shape[1]}x{frame.shape[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
