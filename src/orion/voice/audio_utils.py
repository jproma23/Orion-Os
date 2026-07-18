"""Reamostragem de audio compartilhada entre captura e sintese (Cap 9).

Varios dispositivos desta montagem so aceitam sua taxa nativa (ex.: USB
Audio Device = 44100Hz) e recusam abrir o stream em outra taxa
(`PaErrorCode -9997 Invalid sample rate`) - tanto para gravar quanto para
tocar. Em vez de depender do dispositivo aceitar a taxa que cada consumidor
quer (16kHz para o Whisper, a nativa do Piper para tocar), gravamos/tocamos
na taxa nativa do dispositivo e reamostramos em software.
"""
from __future__ import annotations

import numpy as np


def reamostrar(audio: np.ndarray, taxa_origem: int, taxa_destino: int) -> np.ndarray:
    """Reamostragem por interpolacao linear - suficiente para
    inteligibilidade de fala, sem precisar de scipy so para isso."""
    if taxa_origem == taxa_destino:
        return audio
    duracao_amostras = int(round(len(audio) * taxa_destino / taxa_origem))
    indices_origem = np.arange(len(audio))
    indices_destino = np.linspace(0, len(audio) - 1, duracao_amostras)
    return np.interp(indices_destino, indices_origem, audio).astype(audio.dtype)
