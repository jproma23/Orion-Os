"""Junta os frames da varredura numa panoramica. Roda no Notebook (OpenCV).

Uso: python3 montar_panorama.py <pasta_com_frames> <prefixo_saida>

Gera DUAS versoes para comparar, porque a projecao certa depende do gosto:
  <prefixo>_cilindrica.jpg  -> modo PANORAMA (camera girando; curva as pontas)
  <prefixo>_plana.jpg       -> modo SCANS (affine; achata, tipo scanner)

Os frames devem estar nomeados em ordem esquerda->direita (frame_00, 01...).
Se um modo falhar (cena sem textura para casar bordas), aquela versao vira
uma tira lado-a-lado - assim sempre sai imagem.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2


def costurar(imagens: list, modo: int, saida: str, rotulo: str) -> None:
    status, pano = cv2.Stitcher_create(modo).stitch(imagens)
    if status == cv2.Stitcher_OK:
        cv2.imwrite(saida, pano)
        print(f"ok {rotulo} {pano.shape[1]}x{pano.shape[0]} -> {saida}")
        return
    print(f"{rotulo}: costura falhou (status {status}); tira lado a lado")
    alt = min(im.shape[0] for im in imagens)
    redim = [cv2.resize(im, (int(im.shape[1] * alt / im.shape[0]), alt)) for im in imagens]
    cv2.imwrite(saida, cv2.hconcat(redim))
    print(f"{rotulo}: tira -> {saida}")


def main() -> int:
    pasta = Path(sys.argv[1])
    prefixo = sys.argv[2]

    caminhos = sorted(pasta.glob("frame_*.jpg"))
    imagens = [im for im in (cv2.imread(str(c)) for c in caminhos) if im is not None]
    if len(imagens) < 2:
        print(f"ERRO: so {len(imagens)} frame(s), preciso de 2+", file=sys.stderr)
        return 1
    print(f"costurando {len(imagens)} frames nos dois modos...")

    costurar(imagens, cv2.Stitcher_PANORAMA, f"{prefixo}_cilindrica.jpg", "cilindrica")
    costurar(imagens, cv2.Stitcher_SCANS, f"{prefixo}_plana.jpg", "plana")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
