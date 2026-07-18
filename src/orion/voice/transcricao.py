"""Transcricao de fala com faster-whisper (Cap 9 secao 3, passo 5).

O modelo Whisper exige audio mono a 16kHz - nao ha parametro de taxa de
amostragem no `transcribe()` da lib, entao quem grava (captura_audio.py)
ja usa TAXA_AMOSTRAGEM_PADRAO=16000 para casar com essa exigencia.
"""
from __future__ import annotations

import asyncio

import numpy as np
from faster_whisper import WhisperModel


class Transcritor:
    def __init__(self, modelo: str = "base", idioma: str = "pt") -> None:
        self._modelo = WhisperModel(modelo, device="cpu", compute_type="int8")
        self._idioma = idioma

    async def transcrever(self, audio: np.ndarray) -> str:
        def _transcrever() -> str:
            segmentos, _ = self._modelo.transcribe(audio, language=self._idioma)
            return " ".join(segmento.text.strip() for segmento in segmentos).strip()

        return await asyncio.to_thread(_transcrever)
