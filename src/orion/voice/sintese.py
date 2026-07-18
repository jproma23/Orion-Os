"""Sintese de voz com Piper (Cap 9 secao 3, passos 8-9).

Cancelamento de eco simples (Cap 9 s.7): quem orquestra o Voice Core deve
parar de escutar/transcrever enquanto `falar()` esta rodando (o estado
SPEAKING cobre isso) - este modulo so cuida de sintetizar e reproduzir.

`indice_dispositivo_saida`: nesta montagem o alto-falante interno e o P2
ainda nao aparecem no audio do Linux (falta driver/quirk do codec HDA) -
o padrao usa o dispositivo de audio USB confirmado funcionando
(config/orion.yaml: voice.saida_audio_indice). None deixa o
sounddevice escolher o dispositivo padrao do sistema.
"""
from __future__ import annotations

import asyncio
import io
import wave

import numpy as np
import sounddevice as sd
from piper import PiperVoice

from orion.voice.audio_utils import reamostrar as _reamostrar


class Sintetizador:
    def __init__(self, caminho_modelo: str, indice_dispositivo_saida: int | None = None) -> None:
        self._voz = PiperVoice.load(caminho_modelo)
        self._indice_dispositivo_saida = indice_dispositivo_saida

    async def falar(self, texto: str) -> None:
        def _sintetizar_e_reproduzir() -> None:
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav_out:
                self._voz.synthesize_wav(texto, wav_out)
            buffer.seek(0)
            with wave.open(buffer, "rb") as wav_in:
                dados = wav_in.readframes(wav_in.getnframes())
                taxa = wav_in.getframerate()
            audio = np.frombuffer(dados, dtype=np.int16)

            # o dispositivo de saida pode nao suportar a taxa nativa do
            # Piper (ex.: USB Audio Device so aceita 44100Hz) - reamostra
            # em vez de deixar o PortAudio recusar com "Invalid sample rate".
            taxa_dispositivo = int(
                sd.query_devices(self._indice_dispositivo_saida)["default_samplerate"]
            )
            if taxa_dispositivo != taxa:
                audio = _reamostrar(audio, taxa, taxa_dispositivo)
                taxa = taxa_dispositivo

            sd.play(audio, samplerate=taxa, device=self._indice_dispositivo_saida)
            sd.wait()

        await asyncio.to_thread(_sintetizar_e_reproduzir)
