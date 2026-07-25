"""Captura UM frame estavel da webcam e salva. Roda no Notebook (onde a
camera esta, /dev/video0). Chamado uma vez por posicao do pan pela panoramica.

Uso: python3 capturar_frame.py <caminho_saida.jpg> [indice_camera] [flip]

`flip=1` desfaz o espelhamento "selfie" da webcam (mesmo cv2.flip que o
pipeline de visao usa), para os frames baterem com a orientacao do mundo
real - assim o sentido do pan casa com o sentido da imagem na costura.

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
    flip = len(sys.argv) > 3 and sys.argv[3] == "1"

    # Duas tentativas: a webcam as vezes trava (select() timeout) quando foi
    # esbarrada; reabrir do zero costuma resolver.
    frame = None
    for tentativa in range(2):
        cam = cv2.VideoCapture(indice, cv2.CAP_V4L2)
        if cam.isOpened():
            cam.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            for _ in range(QUADROS_DESCARTADOS):
                ok, lido = cam.read()
                if ok:
                    frame = lido
        cam.release()
        if frame is not None:
            break
        print(f"  (tentativa {tentativa + 1} falhou, reabrindo)", file=sys.stderr)

    if frame is None:
        print("ERRO: nao capturou quadro", file=sys.stderr)
        return 1
    if flip:
        frame = cv2.flip(frame, 1)
    cv2.imwrite(saida, frame)
    print(f"ok {saida} {frame.shape[1]}x{frame.shape[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
