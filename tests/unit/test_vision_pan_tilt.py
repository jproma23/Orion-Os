"""Testes do calculo de correcao Pan/Tilt (Cap 8 s.8). Sem dependencias pesadas."""
from orion.vision.pan_tilt import CalculadoraPanTilt, LimitesPanTilt


def test_alvo_no_centro_nao_precisa_de_correcao():
    calculadora = CalculadoraPanTilt()
    pan, tilt = calculadora.calcular_correcao(
        centro_alvo_x=320, centro_alvo_y=240, largura_frame=640, altura_frame=480, intervalo_s=0.1
    )
    assert pan == 0.0
    assert tilt == 0.0


def test_alvo_a_direita_gera_pan_positivo():
    calculadora = CalculadoraPanTilt()
    pan, _ = calculadora.calcular_correcao(
        centro_alvo_x=640, centro_alvo_y=240, largura_frame=640, altura_frame=480, intervalo_s=0.5
    )
    assert pan > 0


def test_alvo_acima_do_centro_gera_tilt_para_cima():
    calculadora = CalculadoraPanTilt()
    _, tilt = calculadora.calcular_correcao(
        centro_alvo_x=320, centro_alvo_y=0, largura_frame=640, altura_frame=480, intervalo_s=0.5
    )
    assert tilt > 0


def test_velocidade_maxima_limita_o_passo():
    limites = LimitesPanTilt(velocidade_max_graus_s=10.0)
    calculadora = CalculadoraPanTilt(limites)

    pan, _ = calculadora.calcular_correcao(
        centro_alvo_x=640, centro_alvo_y=240, largura_frame=640, altura_frame=480, intervalo_s=0.1
    )

    assert abs(pan) <= 10.0 * 0.1 + 1e-9


def test_angulo_nunca_ultrapassa_os_limites():
    limites = LimitesPanTilt(pan_min_graus=-30, pan_max_graus=30, velocidade_max_graus_s=1000)
    calculadora = CalculadoraPanTilt(limites)

    for _ in range(50):
        pan, _ = calculadora.calcular_correcao(
            centro_alvo_x=640, centro_alvo_y=240, largura_frame=640, altura_frame=480, intervalo_s=1.0
        )

    assert -30 <= pan <= 30


def test_alvo_centralizado_dentro_da_tolerancia():
    limites = LimitesPanTilt(tolerancia_centralizado_percent=5.0)
    calculadora = CalculadoraPanTilt(limites)

    assert calculadora.alvo_centralizado(322, 241, 640, 480) is True
    assert calculadora.alvo_centralizado(500, 241, 640, 480) is False
