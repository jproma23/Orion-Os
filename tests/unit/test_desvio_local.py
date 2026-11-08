"""Desvio local do EXPLORE (2026-11-01, EDR-0037).

Caso real que o originou: com 25,7 cm livres a frente o EXPLORE mandou andar
30 cm e bateu na comoda; a IA mandou explorar de novo e ele bateu na parede.
O dono: o problema nao e decidir andar, e nao desviar.
"""
import math

import pytest

from motion_core.navigation.desvio_local import (
    AVANCAR,
    BLOQUEADO,
    GIRAR,
    escolher_passo,
    livre_na_direcao,
)


def _sala(padrao=300.0, **faixas):
    """360 raios a `padrao` cm, com faixas sobrescritas: f70_110=25 poe 25 cm
    de 70 a 110 graus (0 esquerda, 90 frente, 180 direita)."""
    raios = [padrao] * 360
    for nome, distancia in faixas.items():
        inicio, fim = (int(x) for x in nome[1:].split("_"))
        for angulo in range(inicio, fim + 1):
            raios[angulo % 360] = distancia
    return raios


def _parede_a_frente(distancia_cm, meia_abertura=60, padrao=300.0):
    """Parede RETA perpendicular a frente, a `distancia_cm` do LiDAR: o raio
    no angulo a mede distancia / cos(a - 90). Um leque com a mesma distancia
    em todos os angulos fica mais perto nas bordas do corredor."""
    raios = [padrao] * 360
    for delta in range(-meia_abertura, meia_abertura + 1):
        raios[(90 + delta) % 360] = distancia_cm / math.cos(math.radians(delta))
    return raios


def test_frente_aberta_avanca_o_passo_inteiro():
    passo = escolher_passo(_sala())
    assert passo.acao == AVANCAR
    assert passo.valor == 30.0


def test_frente_a_65_cm_avanca_so_o_que_cabe_ate_o_limite():
    passo = escolher_passo(_parede_a_frente(65.0), limite_cm=50.0)
    assert passo.acao == AVANCAR
    assert passo.valor == 15.0


def test_caso_da_comoda_gira_para_o_lado_aberto_e_nao_avanca():
    """Frente a 25,7 cm (a comoda), direita fechada a 30 cm, esquerda aberta."""
    raios = _sala(f60_120=25.7, f121_200=30.0)
    passo = escolher_passo(raios, limite_cm=50.0)
    assert passo.acao == GIRAR
    assert passo.valor < 0, "a esquerda e o lado aberto (giro negativo = esquerda)"


def test_prefere_o_menor_giro_que_ja_permite_passo_inteiro():
    """Caixa estreita a 55 cm: a 60 graus ja passa - nao da meia-volta."""
    raios = _sala(f85_95=55.0)
    passo = escolher_passo(raios, limite_cm=50.0)
    assert passo.acao == GIRAR
    assert abs(passo.valor) <= 60


def test_cercado_por_todos_os_lados_fica_bloqueado():
    passo = escolher_passo(_sala(padrao=30.0), limite_cm=50.0)
    assert passo.acao == BLOQUEADO


def test_frente_sem_eco_nao_e_caminho_livre():
    """Sem eco na frente pode ser vidro ou algo perto demais: nao avanca."""
    raios = _sala()
    for angulo in range(60, 121):
        raios[angulo] = None
    assert livre_na_direcao(raios, 90.0, 25.0) is None
    assert escolher_passo(raios).acao != AVANCAR


def test_sem_leitura_fica_bloqueado():
    assert escolher_passo(None).acao == BLOQUEADO


def test_o_avanco_nunca_passa_do_limite():
    for frente in (61.0, 70.0, 79.0, 85.0, 200.0):
        passo = escolher_passo(_parede_a_frente(frente), limite_cm=50.0)
        assert passo.acao == AVANCAR
        assert frente - passo.valor >= 50.0 - 0.5, f"frente {frente}: andou {passo.valor}"


def test_frente_meio_cega_com_pontos_longe_nos_cantos_nao_avanca():
    """Os cantos do corredor medem longe, o meio nao tem eco: nao e livre."""
    raios = _sala()
    for angulo in range(72, 109):
        raios[angulo] = None
    assert escolher_passo(raios).acao != AVANCAR


# --- 08/11: auto alinhamento - meio da passagem, paralelo a parede, meio do corredor ---

from motion_core.navigation.desvio_local import ajuste_de_rumo  # noqa: E402


def _paredes(esquerda_cm=None, direita_cm=None, robo_virado_direita_graus=0.0, fundo=None):
    """Raios de um corredor reto, na convencao do projeto (0 esquerda, 90 frente).

    Coordenadas do robo: x para a esquerda, y para a frente. Com o robo virado
    para a DIREITA de theta, a direcao do corredor fica theta a esquerda dele:
    cada parede e a reta x = c + tan(theta)*y. Fundo None = sem eco nas pontas.
    """
    m = math.tan(math.radians(robo_virado_direita_graus))
    raios = [fundo] * 360
    for angulo in range(360):
        a = math.radians(angulo)
        cos_a, sin_a = math.cos(a), math.sin(a)
        melhor = None
        for c in (esquerda_cm, None if direita_cm is None else -direita_cm):
            if c is None:
                continue
            den = cos_a - m * sin_a
            if abs(den) < 1e-9:
                continue
            r = c / den
            if r > 0 and (melhor is None or r < melhor):
                melhor = r
        if melhor is not None and melhor < 800:
            raios[angulo] = round(melhor, 1)
    return raios


def test_paralelo_a_parede_nao_corrige():
    assert ajuste_de_rumo(_paredes(esquerda_cm=40)) is None


def test_virado_para_a_direita_com_parede_a_esquerda_gira_para_a_esquerda():
    giro, motivo = ajuste_de_rumo(_paredes(esquerda_cm=40, robo_virado_direita_graus=15))
    assert giro == pytest.approx(-15, abs=1.5)
    assert "esquerda" in motivo


def test_virado_para_a_esquerda_com_parede_a_direita_gira_para_a_direita():
    giro, _ = ajuste_de_rumo(_paredes(direita_cm=45, robo_virado_direita_graus=-12))
    assert giro == pytest.approx(12, abs=1.5)


def test_no_corredor_vai_para_o_meio():
    """Paralelo, mas 20 cm mais perto da direita: gira para a esquerda."""
    giro, motivo = ajuste_de_rumo(_paredes(esquerda_cm=60, direita_cm=20))
    assert giro < 0
    assert giro == pytest.approx(-math.degrees(math.atan(20 / 100)), abs=1.5)
    assert "corredor" in motivo


def test_desvio_pequeno_e_ruido():
    assert ajuste_de_rumo(_paredes(esquerda_cm=40, robo_virado_direita_graus=3)) is None


def test_sem_parede_perto_nao_corrige():
    assert ajuste_de_rumo([300.0] * 360) is None
    assert ajuste_de_rumo(_paredes(esquerda_cm=150)) is None


def test_moveis_soltos_nao_sao_parede():
    raios = [300.0] * 360
    for angulo in (340, 350, 0, 10, 20):
        raios[angulo] = 30.0 + (angulo % 7) * 6  # pontos espalhados, sem reta
    assert ajuste_de_rumo(raios) is None


def test_gira_para_o_meio_da_porta_e_nao_para_a_borda():
    """Porta a direita: abre de 120 a 160 graus (30 a 70 graus a direita da
    frente). Antes ele girava para a primeira direcao de 30 em 30 que cabia -
    a borda; agora para o meio."""
    raios = [60.0] * 360
    for angulo in range(120, 161):
        raios[angulo] = 400.0
    passo = escolher_passo(raios, limite_cm=50.0, passo_max_cm=30.0, meia_largura_cm=5.0)
    assert passo.acao == GIRAR
    assert passo.valor == pytest.approx(50, abs=10)
    assert "passagem" in passo.motivo
