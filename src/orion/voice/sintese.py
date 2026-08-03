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

from orion.voice import timbre as _timbre
from orion.voice.audio_utils import reamostrar as _reamostrar

#: A VOZ DO FOFAO (2026-08-03) - pedido do dono: "caloroso e presente".
#:
#: O modelo continua o `pt_BR-faber-medium`, que e a voz padrao de portugues do
#: Brasil no Piper. O que muda o carater nao e o modelo, sao estes tres numeros
#: mais o tratamento em `orion.voice.timbre`.
#:
#: `length_scale` levemente acima de 1 deixa a fala menos apressada - quem fala
#: com calma soa mais acolhedor. Nao muito acima: pausado demais vira solene, e
#: solene e frio, o oposto do pedido.
#:
#: `noise_scale` um pouco acima do padrao (0,667) devolve variacao a entoacao.
#: Voz sem variacao soa robotica; variacao demais soa insegura.
#:
#: Nada disso custa latencia da PRIMEIRA frase: o streaming por frases ja
#: garante que o robo comeca a falar antes de terminar de gerar.
VOZ_LENGTH_SCALE = 1.05
VOZ_NOISE_SCALE = 0.70
VOZ_NOISE_W_SCALE = 0.80


class Sintetizador:
    def __init__(
        self,
        caminho_modelo: str,
        indice_dispositivo_saida: int | None = None,
        length_scale: float = VOZ_LENGTH_SCALE,
        noise_scale: float = VOZ_NOISE_SCALE,
        noise_w_scale: float = VOZ_NOISE_W_SCALE,
        aplicar_timbre: bool = True,
    ) -> None:
        self._voz = PiperVoice.load(caminho_modelo)
        self._indice_dispositivo_saida = indice_dispositivo_saida
        self._aplicar_timbre = aplicar_timbre
        # Import tardio e tolerante: versoes antigas do piper nao tem
        # SynthesisConfig. Sem ele a voz sai no padrao, que e o que acontecia
        # ate hoje - nunca deixa de falar por causa de um ajuste de timbre.
        try:
            from piper.config import SynthesisConfig

            self._config = SynthesisConfig(
                length_scale=length_scale,
                noise_scale=noise_scale,
                noise_w_scale=noise_w_scale,
            )
        except Exception:
            self._config = None

    async def falar(self, texto: str) -> None:
        def _sintetizar_e_reproduzir() -> None:
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav_out:
                if self._config is not None:
                    self._voz.synthesize_wav(texto, wav_out, syn_config=self._config)
                else:
                    self._voz.synthesize_wav(texto, wav_out)
            buffer.seek(0)
            with wave.open(buffer, "rb") as wav_in:
                dados = wav_in.readframes(wav_in.getnframes())
                taxa = wav_in.getframerate()
            audio = np.frombuffer(dados, dtype=np.int16)

            # O TIMBRE ANTES DA REAMOSTRAGEM, de proposito: a equalizacao e
            # feita em cima da taxa NATIVA do Piper, onde o espectro esta
            # intacto. Reamostrar primeiro e equalizar depois trabalharia sobre
            # um sinal que ja passou por interpolacao.
            if self._aplicar_timbre:
                audio = _timbre.aquecer(audio, taxa)

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
