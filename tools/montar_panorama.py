"""Junta os frames da varredura numa panoramica. Roda no Notebook (OpenCV).

Uso: python3 montar_panorama.py <pasta_com_frames> <saida.jpg>

Os frames devem estar nomeados em ordem esquerda->direita (frame_00, 01...).
Se o costurador do OpenCV falhar (cena sem textura suficiente para casar as
bordas), cai para uma tira lado-a-lado - assim sempre sai alguma imagem.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2


def main() -> int:
    pasta = Path(sys.argv[1])
    saida = sys.argv[2]

    caminhos = sorted(pasta.glob("frame_*.jpg"))
    imagens = [cv2.imread(str(c)) for c in caminhos]
    imagens = [im for im in imagens if im is not None]
    if len(imagens) < 2:
        print(f"ERRO: so {len(imagens)} frame(s), preciso de 2+", file=sys.stderr)
        return 1
    print(f"costurando {len(imagens)} frames...")

    costurador = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
    status, pano = costurador.stitch(imagens)

    if status == cv2.Stitcher_OK:
        cv2.imwrite(saida, pano)
        print(f"ok panoramica {pano.shape[1]}x{pano.shape[0]} -> {saida}")
        return 0

    # fallback: tira lado a lado, todos na mesma altura
    print(f"costura falhou (status {status}); montando tira lado a lado")
    alt = min(im.shape[0] for im in imagens)
    redim = [cv2.resize(im, (int(im.shape[1] * alt / im.shape[0]), alt)) for im in imagens]
    tira = cv2.hconcat(redim)
    cv2.imwrite(saida, tira)
    print(f"tira {tira.shape[1]}x{tira.shape[0]} -> {saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
