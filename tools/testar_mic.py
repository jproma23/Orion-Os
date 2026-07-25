"""Testa UM microfone: grava, mede o nivel do audio e transcreve com o mesmo
modelo do robo (Whisper base, pt). Serve para descobrir se a transcricao ruim
e do mic (audio fraco/sujo) ou do modelo (audio bom, texto errado).

Roda no Notebook. Uso:
    python3 tools/testar_mic.py [indice_mic] [segundos]
Ex.:  python3 tools/testar_mic.py 4 5

Fale uma frase CONHECIDA durante a gravacao (ex.: "Fofao, faca uma varredura")
para comparar o que voce disse com o que saiu.

IMPORTANTE: se a voz (conversar_fofao.py) estiver rodando, ela pode estar
segurando o mic - pare-a antes, ou teste um indice diferente do que ela usa.
"""
from __future__ import annotations

import asyncio
import sys

import numpy as np
import sounddevice as sd

from orion.voice.transcricao import Transcritor

FS = 16000  # Whisper espera 16 kHz (TAXA_AMOSTRAGEM_PADRAO)


async def main() -> int:
    indice = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != "-" else None
    segs = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0

    print(f"\n>>> Mic {indice}: gravando {segs:.0f}s - FALE AGORA <<<\n")
    audio = sd.rec(int(segs * FS), samplerate=FS, channels=1, device=indice, dtype="float32")
    sd.wait()
    audio = audio.flatten()

    rms = float(np.sqrt(np.mean(audio**2)))
    pico = float(np.max(np.abs(audio)))
    if rms < 0.01:
        nivel = "MUITO BAIXO - mic longe, mudo ou com ganho baixo"
    elif pico > 0.98:
        nivel = "CLIPANDO - alto demais, distorce"
    else:
        nivel = "ok"
    print(f"nivel do audio: RMS={rms:.4f}  pico={pico:.3f}  -> {nivel}")

    print("transcrevendo (Whisper base)...")
    texto = await Transcritor(modelo="base", idioma="pt").transcrever(audio)
    print(f"\ntranscricao: '{texto}'\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
