"""Testes da reamostragem de audio da sintese de voz (Cap 9 s.3, 8-9).

So a logica pura de reamostragem - sem instanciar Piper/sounddevice de
verdade (precisaria do modelo baixado e de hardware). Pula onde numpy nao
esta instalado (ex.: neste Raspberry Pi).
"""
import pytest

pytest.importorskip("numpy")
pytest.importorskip("sounddevice")
pytest.importorskip("piper")

import numpy as np  # noqa: E402

from orion.voice.sintese import _reamostrar  # noqa: E402


def test_mesma_taxa_retorna_array_identico():
    audio = np.array([1, 2, 3, 4], dtype=np.int16)
    resultado = _reamostrar(audio, 22050, 22050)
    assert np.array_equal(resultado, audio)


def test_upsample_aumenta_quantidade_de_amostras():
    audio = np.linspace(0, 1000, 100).astype(np.int16)
    resultado = _reamostrar(audio, 22050, 44100)
    assert len(resultado) == 200


def test_downsample_diminui_quantidade_de_amostras():
    audio = np.linspace(0, 1000, 200).astype(np.int16)
    resultado = _reamostrar(audio, 44100, 22050)
    assert len(resultado) == 100


def test_preserva_dtype():
    audio = np.array([100, 200, 300], dtype=np.int16)
    resultado = _reamostrar(audio, 22050, 44100)
    assert resultado.dtype == np.int16
