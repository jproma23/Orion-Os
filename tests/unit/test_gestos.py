"""Testes do DetectorGestos (Cap 8, gestos do Kamal) - mãos sintéticas,
sem câmera nem mediapipe: só os 21 pontos normalizados.

Vocabulário desde 2026-07-28: joinha = SIM, mão aberta balançando = VEM CÁ.
O "não" (dedo indicador balançando) foi substituído pelo chamado a pedido
do dono.
"""

from orion.vision.gestos import DetectorGestos, eh_joinha, mao_aberta


def _mao_neutra() -> list[tuple[float, float]]:
    """Mão aberta parada - a pose do chamado, sem o movimento."""
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


def _mao_chamando(x: float = 0.5, y: float = 0.35) -> list[tuple[float, float]]:
    """Mão aberta com a ponta do dedo médio (ponto 12) numa posição dada -
    é esse ponto que o detector segue para medir o balanço."""
    pontos = _mao_neutra()
    pontos[12] = (x, y)
    return pontos


def _punho() -> list[tuple[float, float]]:
    """Mão fechada: nem joinha (polegar dobrado), nem aberta."""
    pontos = [(0.5, 0.5)] * 21
    for ponta, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        pontos[pip] = (0.5, 0.45)
        pontos[ponta] = (0.5, 0.60)
    pontos[2] = (0.45, 0.55)
    pontos[3] = (0.45, 0.57)
    pontos[4] = (0.45, 0.60)
    return pontos


def test_poses_estaticas():
    assert eh_joinha(_joinha())
    assert not eh_joinha(_mao_neutra())
    assert mao_aberta(_mao_neutra())
    assert not mao_aberta(_punho())
    assert not mao_aberta(_joinha())


def test_joinha_estavel_vira_sim():
    detector = DetectorGestos(frames_joinha=3)
    resultado = None
    for i in range(4):
        resultado = detector.alimentar(_joinha(), agora=10.0 + i * 0.2) or resultado
    assert resultado == "sim"


def test_sem_mao_no_quadro_nao_dispara_nada():
    detector = DetectorGestos()
    for i in range(10):
        assert detector.alimentar(None, agora=10.0 + i * 0.2) is None


def test_mao_aberta_parada_nao_chama():
    """Mão levantada e parada NÃO é chamado - falta o balanço. Sem isso,
    qualquer pessoa com a mão erguida no quadro mandaria o robô vir."""
    detector = DetectorGestos()
    for i in range(8):
        assert detector.alimentar(_mao_neutra(), agora=10.0 + i * 0.2) is None


def test_mao_balancando_na_horizontal_vira_vem():
    detector = DetectorGestos()
    resultado = None
    for i, x in enumerate((0.40, 0.50, 0.40, 0.50, 0.40, 0.50)):
        resultado = detector.alimentar(_mao_chamando(x=x), agora=10.0 + i * 0.2) or resultado
    assert resultado == "vem"


def test_mao_balancando_na_vertical_vira_vem():
    """Chamar com a mão é vertical para muita gente - exigir só o eixo
    horizontal deixaria essas pessoas sem o gesto."""
    detector = DetectorGestos()
    resultado = None
    for i, y in enumerate((0.30, 0.40, 0.30, 0.40, 0.30, 0.40)):
        resultado = detector.alimentar(_mao_chamando(y=y), agora=10.0 + i * 0.2) or resultado
    assert resultado == "vem"


def test_punho_balancando_nao_chama():
    """O movimento sozinho não basta: sem a mão ABERTA não é chamado."""
    detector = DetectorGestos()
    for i, x in enumerate((0.40, 0.50, 0.40, 0.50, 0.40, 0.50)):
        pontos = _punho()
        pontos[12] = (x, 0.60)
        assert detector.alimentar(pontos, agora=10.0 + i * 0.2) is None


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
