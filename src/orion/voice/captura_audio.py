"""Captura de audio (Cap 9 secao 2-3, 6).

sounddevice e bloqueante - cada gravacao roda em thread separada via
`asyncio.to_thread`. Dois microfones confirmados nesta montagem: o
embutido na webcam USB e o "USB Audio Device" do notebook - Cap 9 s.6 pede
selecao automatica do melhor canal por ruido/intensidade/estabilidade.
"""
from __future__ import annotations

import asyncio
import logging

import numpy as np
import sounddevice as sd

from orion.voice.audio_utils import reamostrar

logger = logging.getLogger("orion.voice.captura_audio")

TAXA_AMOSTRAGEM_PADRAO = 16000  # Whisper espera 16kHz


class ErroAudio(Exception):
    """Falha ao capturar audio (Cap 9 s.5: voice.audio_error)."""


def listar_dispositivos_entrada() -> list[dict]:
    """Lista os dispositivos de captura de audio disponiveis."""
    dispositivos = sd.query_devices()
    return [
        {"indice": i, "nome": d["name"], "canais_entrada": d["max_input_channels"]}
        for i, d in enumerate(dispositivos)
        if d["max_input_channels"] > 0
    ]


async def gravar_trecho(
    indice_dispositivo: int,
    duracao_s: float,
    taxa_amostragem: int = TAXA_AMOSTRAGEM_PADRAO,
) -> np.ndarray:
    """Grava `duracao_s` segundos de audio mono do dispositivo indicado, na
    taxa pedida (`taxa_amostragem`, 16kHz por padrao - o que o Whisper
    espera).

    Varios dispositivos desta montagem recusam abrir o stream em qualquer
    taxa que nao seja a nativa deles (`PaErrorCode -9997 Invalid sample
    rate`) - por isso gravamos na taxa nativa do dispositivo e reamostramos
    depois, em vez de pedir `taxa_amostragem` direto ao PortAudio.
    """

    def _gravar() -> np.ndarray:
        try:
            taxa_nativa = int(sd.query_devices(indice_dispositivo)["default_samplerate"])
            audio = sd.rec(
                int(duracao_s * taxa_nativa),
                samplerate=taxa_nativa,
                channels=1,
                dtype="float32",
                device=indice_dispositivo,
            )
            sd.wait()
        except Exception as erro:
            raise ErroAudio(
                f"Falha ao gravar do dispositivo {indice_dispositivo}: {erro}"
            ) from erro
        audio = audio.flatten()
        if taxa_nativa != taxa_amostragem:
            audio = reamostrar(audio, taxa_nativa, taxa_amostragem)
        return audio

    return await asyncio.to_thread(_gravar)


def calcular_qualidade(audio: np.ndarray) -> float:
    """Estimativa simples de qualidade de sinal (Cap 9 s.6): intensidade
    (RMS) penalizada por instabilidade (desvio padrao alto). Quanto maior,
    melhor."""
    if audio.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(audio))))
    estabilidade = 1.0 / (1.0 + float(np.std(audio)))
    return rms * estabilidade


class SeletorMicrofone:
    """Grava um trecho curto de cada microfone candidato e escolhe o de
    melhor qualidade de sinal (Cap 9 s.6)."""

    def __init__(self, indices_candidatos: list[int], duracao_teste_s: float = 0.5) -> None:
        self._indices_candidatos = indices_candidatos
        self._duracao_teste_s = duracao_teste_s
        self._indice_escolhido: int | None = None

    async def escolher_melhor(self) -> int:
        melhor_indice = None
        melhor_qualidade = -1.0
        for indice in self._indices_candidatos:
            try:
                audio = await gravar_trecho(indice, self._duracao_teste_s)
            except ErroAudio:
                logger.warning("Microfone %d nao respondeu, ignorando", indice)
                continue
            qualidade = calcular_qualidade(audio)
            if qualidade > melhor_qualidade:
                melhor_qualidade = qualidade
                melhor_indice = indice

        if melhor_indice is None:
            raise ErroAudio("Nenhum microfone candidato respondeu")

        self._indice_escolhido = melhor_indice
        logger.info("Microfone escolhido: indice %d (qualidade=%.4f)", melhor_indice, melhor_qualidade)
        return melhor_indice

    @property
    def indice_escolhido(self) -> int | None:
        return self._indice_escolhido
