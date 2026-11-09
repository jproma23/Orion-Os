"""Detector de camera sem sinal (2026-11-01) - so numpy, roda sem camera."""
import pytest

np = pytest.importorskip("numpy")

from orion.vision.sem_sinal import DetectorSemSinal  # noqa: E402


def _vivo(semente):
    return np.random.default_rng(semente).integers(40, 200, size=(480, 640, 3), dtype=np.uint8)


def test_quadro_preto_por_varios_quadros_vira_sem_sinal():
    detector = DetectorSemSinal(quadros_seguidos=3)
    preto = np.zeros((480, 640, 3), dtype=np.uint8)
    resultados = [detector.avaliar(preto).sem_sinal for _ in range(3)]
    assert resultados == [False, False, True]


def test_imagem_fixa_reenviada_vira_sem_sinal():
    """A tela "NO SIGNAL" nao e preta uniforme (tem texto), mas nao muda."""
    detector = DetectorSemSinal(quadros_seguidos=3)
    fixa = _vivo(1)
    resultados = [detector.avaliar(fixa) for _ in range(4)]
    assert resultados[-1].sem_sinal
    assert resultados[-1].motivo == "imagem_congelada"


def test_camera_viva_nunca_vira_sem_sinal():
    detector = DetectorSemSinal(quadros_seguidos=3)
    assert not any(detector.avaliar(_vivo(i)).sem_sinal for i in range(20))


def test_um_quadro_bom_zera_a_contagem():
    detector = DetectorSemSinal(quadros_seguidos=3)
    preto = np.zeros((480, 640, 3), dtype=np.uint8)
    detector.avaliar(preto)
    detector.avaliar(preto)
    detector.avaliar(_vivo(5))
    assert not detector.avaliar(preto).sem_sinal
