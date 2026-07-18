"""Testes de captura de audio (Cap 9 s.2-3, 6).

Pula graciosamente onde `sounddevice` nao esta instalado (ex.: neste
Raspberry Pi - Voice Core roda so no Notebook, Cap 9 s.2).
"""
import pytest

pytest.importorskip("numpy")
pytest.importorskip("sounddevice")

import numpy as np  # noqa: E402

from orion.voice.captura_audio import calcular_qualidade  # noqa: E402


def test_qualidade_silencio_e_zero():
    silencio = np.zeros(1000, dtype=np.float32)
    assert calcular_qualidade(silencio) == 0.0


def test_qualidade_array_vazio_e_zero():
    assert calcular_qualidade(np.array([], dtype=np.float32)) == 0.0


def test_sinal_mais_forte_tem_qualidade_maior():
    fraco = np.full(1000, 0.01, dtype=np.float32)
    forte = np.full(1000, 0.5, dtype=np.float32)
    assert calcular_qualidade(forte) > calcular_qualidade(fraco)


def test_sinal_instavel_penalizado_frente_a_estavel_de_mesmo_rms():
    rng = np.random.default_rng(42)
    # sinal constante: RMS = 0.3, desvio padrao = 0 (sem instabilidade)
    estavel = np.full(1000, 0.3, dtype=np.float32)
    # ruido de media zero com desvio padrao 0.3 tem RMS ~0.3 tambem (para
    # sinal de media zero, RMS == desvio padrao) - mesma intensidade, mas
    # instavel (variancia alta)
    instavel = rng.normal(0, 0.3, 1000).astype(np.float32)

    assert calcular_qualidade(estavel) > calcular_qualidade(instavel)
