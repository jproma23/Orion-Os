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


def test_sincronizar_move_a_posicao_assumida():
    calculadora = CalculadoraPanTilt()
    calculadora.sincronizar(-60, 10)
    assert calculadora.pan_atual == -60.0
    assert calculadora.tilt_atual == 10.0


def test_sincronizar_respeita_os_limites():
    """Se alguem mandou a cabeca para fora da faixa, a estimativa acompanha o
    que o servo consegue fazer - nao o que foi pedido."""
    limites = LimitesPanTilt(pan_min_graus=-30, pan_max_graus=30)
    calculadora = CalculadoraPanTilt(limites)

    calculadora.sincronizar(-500, 0)
    assert calculadora.pan_atual == -30.0
    calculadora.sincronizar(500, 0)
    assert calculadora.pan_atual == 30.0


def test_sem_sincronizar_a_cabeca_pula_de_uma_vez():
    """A regressao de 2026-07-29, medida em graus.

    A ronda dos comportamentos joga a cabeca para -60. A visao continua
    achando que esta em +20 e calcula a correcao a partir dessa base. Sem
    ressincronizar, o angulo pedido esta ~80 graus longe de onde a cabeca de
    fato esta; com ressincronizar, o passo e pequeno.
    """
    # velocidade alta o bastante para NAO ser o limitador aqui - o que se mede
    # e o erro da BASE, nao o teto de velocidade.
    limites = LimitesPanTilt(velocidade_max_graus_s=1000)
    alvo_quase_centrado = dict(
        centro_alvo_x=340, centro_alvo_y=240, largura_frame=640, altura_frame=480, intervalo_s=0.1
    )

    desinformada = CalculadoraPanTilt(limites)
    desinformada.sincronizar(20, 0)  # a visao acha que esta em +20
    pan_errado, _ = desinformada.calcular_correcao(**alvo_quase_centrado)

    informada = CalculadoraPanTilt(limites)
    informada.sincronizar(-60, 0)  # ...e a cabeca esta em -60 de verdade
    pan_certo, _ = informada.calcular_correcao(**alvo_quase_centrado)

    posicao_real = -60.0
    assert abs(pan_errado - posicao_real) > 75, "sem sincronizar, o salto e enorme"
    assert abs(pan_certo - posicao_real) < 5, "sincronizado, o passo e pequeno"
