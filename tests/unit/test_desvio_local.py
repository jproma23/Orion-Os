"""Desvio local do EXPLORE (2026-11-01, EDR-0037).

Caso real que o originou: com 25,7 cm livres a frente o EXPLORE mandou andar
30 cm e bateu na comoda; a IA mandou explorar de novo e ele bateu na parede.
O dono: o problema nao e decidir andar, e nao desviar.
"""
import math

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
