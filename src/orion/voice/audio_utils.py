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


#: Passo do envelope da fala (s). 40 ms = 25 pontos por segundo: segue as
#: silabas (~4-6/s) sem mandar um evento enorme.
PASSO_ENVELOPE_S = 0.04


def envelope_da_fala(
    audio: np.ndarray, taxa: int, passo_s: float = PASSO_ENVELOPE_S
) -> list[float]:
    """Volume da fala a cada `passo_s`, de 0 a 1 - para a BOCA do rosto (2026-11-12).

    RMS por janela, normalizado pelo percentil 95 (e nao pelo maximo: um
    estalo isolado nao pode deixar o resto da frase com a boca quase fechada).
    Abaixo de 8% vira 0 - pausa entre palavras fecha a boca.
    """
    amostras = np.asarray(audio, dtype=np.float32).ravel()
    janela = max(1, int(taxa * passo_s))
    if amostras.size < janela:
        return []
    n = amostras.size // janela
    blocos = amostras[: n * janela].reshape(n, janela)
    rms = np.sqrt(np.mean(blocos * blocos, axis=1))
    referencia = float(np.percentile(rms, 95)) or 1.0
    niveis = np.clip(rms / referencia, 0.0, 1.0)
    niveis[niveis < 0.08] = 0.0
    return [round(float(v), 2) for v in niveis]
