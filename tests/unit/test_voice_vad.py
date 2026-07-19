"""Testes do detector de atividade sonora (src/orion/voice/vad.py).

Audio sintetico via numpy: "silencio" = ruido de baixa amplitude,
"fala" = senoide de amplitude bem maior.
"""
import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from orion.voice.vad import DetectorAtividadeSonora  # noqa: E402


def _silencio(amplitude: float = 0.001, n: int = 16000) -> np.ndarray:
    rng = np.random.default_rng(42)
    return (rng.standard_normal(n) * amplitude).astype("float32")


def _som_alto(amplitude: float = 0.2, n: int = 16000) -> np.ndarray:
    t = np.linspace(0, 1, n, dtype="float32")
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype("float32")


def test_silencio_e_pulado_e_contado():
    detector = DetectorAtividadeSonora()
    for _ in range(5):
        assert detector.tem_som(_silencio()) is False
    assert detector.janelas_puladas == 5


def test_som_alto_passa_pelo_portao():
    detector = DetectorAtividadeSonora()
    detector.tem_som(_silencio())  # aprende o piso da "sala"
    assert detector.tem_som(_som_alto()) is True


def test_piso_adapta_a_sala_mais_barulhenta():
    """Numa sala com ruido de fundo maior, som pouco acima do ruido nao
    dispara - o piso adaptativo acompanha o ambiente."""
    detector = DetectorAtividadeSonora(fator_acima_do_ruido=2.5, rms_minimo=0.003)
    for _ in range(10):
        detector.tem_som(_silencio(amplitude=0.02))  # sala barulhenta
    # som apenas ~1.4x o ruido de fundo: abaixo do fator 2.5 -> ignorado
    assert detector.tem_som(_silencio(amplitude=0.028)) is False
    # fala de verdade (bem acima do fundo) -> dispara mesmo na sala barulhenta
    assert detector.tem_som(_som_alto(amplitude=0.3)) is True


def test_rms_minimo_evita_disparo_em_sala_muito_silenciosa():
    """Com piso quase zero, qualquer sussurro seria 'fator x piso' - o
    limiar absoluto rms_minimo segura disparos por ruido infimo."""
    detector = DetectorAtividadeSonora(fator_acima_do_ruido=2.5, rms_minimo=0.003)
    for _ in range(10):
        detector.tem_som(_silencio(amplitude=0.0001))
    # 3x o piso, mas ainda abaixo do rms_minimo absoluto -> ignorado
    assert detector.tem_som(_silencio(amplitude=0.0003)) is False
