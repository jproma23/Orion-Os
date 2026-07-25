"""Testes do DetectorGestos (Cap 8, gestos do Kamal) - mãos sintéticas,
sem câmera nem mediapipe: só os 21 pontos normalizados."""
import pytest

from orion.vision.gestos import DetectorGestos, eh_joinha, indicador_apontando


def _mao_neutra() -> list[tuple[float, float]]:
    """Mão aberta genérica: nada de joinha, nada de indicador solitário."""
    pontos = [(0.5, 0.5)] * 21
    # todos os dedos esticados (pontas acima das juntas)
    for ponta, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        pontos[pip] = (0.5, 0.45)
        pontos[ponta] = (0.5, 0.35)
    pontos[2] = (0.42, 0.55)
    pontos[3] = (0.40, 0.53)
    pontos[4] = (0.38, 0.52)
    return pontos


def _joinha() -> list[tuple[float, float]]:
    pontos = [(0.5, 0.5)] * 21
    # polegar esticado para cima
    pontos[2] = (0.45, 0.60)
    pontos[3] = (0.45, 0.52)
    pontos[4] = (0.45, 0.45)
    # os quatro dedos dobrados (ponta abaixo da junta)
    for ponta, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        pontos[pip] = (0.55, 0.55)
        pontos[ponta] = (0.55, 0.62)
    return pontos


def _indicador(x_ponta: float) -> list[tuple[float, float]]:
    pontos = [(0.5, 0.5)] * 21
    # indicador esticado
    pontos[6] = (x_ponta, 0.45)
    pontos[8] = (x_ponta, 0.30)
    # médio/anelar/mindinho dobrados
    for ponta, pip in ((12, 10), (16, 14), (20, 18)):
        pontos[pip] = (0.55, 0.55)
        pontos[ponta] = (0.55, 0.62)
    # polegar dobrado (não é joinha)
    pontos[2] = (0.45, 0.55)
    pontos[3] = (0.45, 0.57)
    pontos[4] = (0.45, 0.60)
    return pontos


def test_poses_estaticas():
    assert eh_joinha(_joinha())
    assert not eh_joinha(_mao_neutra())
    assert indicador_apontando(_indicador(0.5))
    assert not indicador_apontando(_mao_neutra())
    assert not indicador_apontando(_joinha())


def test_joinha_estavel_vira_sim():
    detector = DetectorGestos(frames_joinha=3)
    resultado = None
    for i in range(4):
        resultado = detector.alimentar(_joinha(), agora=10.0 + i * 0.2) or resultado
    assert resultado == "sim"


def test_mao_neutra_nao_dispara_nada():
    detector = DetectorGestos()
    for i in range(10):
        assert detector.alimentar(_mao_neutra(), agora=10.0 + i * 0.2) is None


def test_indicador_parado_nao_e_nao():
    """Só apontar (ex.: pra tela) não pode virar 'não' - falta o balanço."""
    detector = DetectorGestos()
    for i in range(8):
        assert detector.alimentar(_indicador(0.5), agora=10.0 + i * 0.2) is None


def test_indicador_balancando_vira_nao():
    detector = DetectorGestos()
    resultado = None
    xs = (0.40, 0.50, 0.40, 0.50, 0.40, 0.50)
    for i, x in enumerate(xs):
        r = detector.alimentar(_indicador(x), agora=10.0 + i * 0.2)
        resultado = r or resultado
    assert resultado == "nao"


def test_cooldown_segura_repeticao():
    detector = DetectorGestos(frames_joinha=3, cooldown_s=3.0)
    for i in range(4):
        detector.alimentar(_joinha(), agora=10.0 + i * 0.2)
    # logo depois do "sim", mais joinha NAO repete (crianca segura o gesto)
    for i in range(4):
        assert detector.alimentar(_joinha(), agora=11.0 + i * 0.2) is None
    # passado o cooldown, pode de novo
    resultado = None
    for i in range(4):
        resultado = detector.alimentar(_joinha(), agora=15.0 + i * 0.2) or resultado
    assert resultado == "sim"
