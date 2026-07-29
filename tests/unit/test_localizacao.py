"""Testes da estimativa de direcao (GCC-PHAT).

O que estes testes protegem, em ordem de importancia:

1. **O SINAL.** Trocar esquerda com direita e o erro classico deste tipo de
   codigo, e ele nao aparece sozinho: o robo simplesmente vira a cabeca
   para o lado errado, e quem olha pensa que o algoritmo "nao funciona".
   Aqui o atraso e IMPOSTO em amostras conhecidas, entao o lado esperado e
   fato, nao opiniao.
2. Que silencio e entrada invalida devolvam None em vez de um angulo
   inventado - senao a cabeca treme com ruido de fundo.
3. Que o limite fisico seja respeitado (nada de angulo alem de +-90).
"""
from __future__ import annotations

import pytest
np = pytest.importorskip("numpy", reason="localizacao de voz requer numpy")

from orion.voice.localizacao import LocalizadorDirecao

TAXA = 16000
SEPARACAO = 0.20  # 20 cm entre os microfones


def _voz_sintetica(n: int, semente: int = 7) -> np.ndarray:
    """Ruido de banda larga - imita melhor a fala do que um tom puro.

    Tom puro seria pessimo aqui: uma senoide e periodica, entao a
    correlacao teria varios picos iguais e o atraso seria ambiguo. Fala
    real tem banda larga, que e o que faz o GCC-PHAT funcionar.
    """
    rng = np.random.default_rng(semente)
    return rng.standard_normal(n)


def _par_com_atraso(atraso_amostras: int, n: int = 4096):
    """Devolve (esquerda, direita) onde a DIREITA chega `atraso` depois.

    Direita atrasada = o som chegou primeiro na esquerda = a fonte esta do
    lado ESQUERDO.
    """
    base = _voz_sintetica(n + abs(atraso_amostras) + 16)
    inicio = abs(atraso_amostras) + 8
    esquerda = base[inicio : inicio + n]
    direita = base[inicio - atraso_amostras : inicio - atraso_amostras + n]
    return esquerda, direita


def test_fonte_a_esquerda_da_angulo_negativo():
    """Direita atrasada em 6 amostras -> fonte a esquerda -> angulo < 0."""
    loc = LocalizadorDirecao(TAXA, SEPARACAO)
    esquerda, direita = _par_com_atraso(6)
    r = loc.estimar(esquerda, direita)
    assert r is not None, "deveria ter achado uma direcao"
    assert r.angulo_graus < 0, (
        "fonte a ESQUERDA tem que dar angulo negativo, veio %.1f" % r.angulo_graus
    )
    assert r.lado == "esquerda", "lado veio '%s'" % r.lado


def test_fonte_a_direita_da_angulo_positivo():
    """Espelho exato do teste anterior - se um passar e o outro nao, o
    sinal esta invertido em algum lugar."""
    loc = LocalizadorDirecao(TAXA, SEPARACAO)
    esquerda, direita = _par_com_atraso(-6)
    r = loc.estimar(esquerda, direita)
    assert r is not None
    assert r.angulo_graus > 0, (
        "fonte a DIREITA tem que dar angulo positivo, veio %.1f" % r.angulo_graus
    )
    assert r.lado == "direita"


def test_fonte_no_centro():
    """Sem atraso -> a frente (ou atras: ver ambiguidade documentada)."""
    loc = LocalizadorDirecao(TAXA, SEPARACAO)
    esquerda, direita = _par_com_atraso(0)
    r = loc.estimar(esquerda, direita)
    assert r is not None
    assert abs(r.angulo_graus) < 15.0, "centro veio como %.1f graus" % r.angulo_graus
    assert r.lado == "centro"


@pytest.mark.parametrize("atraso", [-8, -5, -3, 0, 3, 5, 8])
def test_recupera_o_atraso_imposto(atraso):
    """O atraso estimado tem que bater com o que foi imposto.

    Tolerancia de 1 amostra: a interpolacao parabolica melhora, mas nao
    faz milagre com 9 amostras cobrindo 180 graus.
    """
    loc = LocalizadorDirecao(TAXA, SEPARACAO)
    esquerda, direita = _par_com_atraso(atraso)
    r = loc.estimar(esquerda, direita)
    assert r is not None, "atraso %d nao produziu estimativa" % atraso
    # o sinal interno pode ser oposto ao imposto conforme a convencao;
    # o que importa e o MODULO bater e o lado estar certo.
    assert abs(abs(r.atraso_amostras) - abs(atraso)) <= 1.0, (
        "impus %d amostras, estimou %.2f" % (atraso, r.atraso_amostras)
    )


def test_silencio_nao_tem_direcao():
    loc = LocalizadorDirecao(TAXA, SEPARACAO)
    zeros = np.zeros(2048)
    assert loc.estimar(zeros, zeros) is None


def test_entrada_invalida_devolve_none():
    loc = LocalizadorDirecao(TAXA, SEPARACAO)
    assert loc.estimar(np.zeros(10), np.zeros(10)) is None          # curto demais
    assert loc.estimar(np.ones(2048), np.ones(64)) is None          # tamanhos diferentes


def test_angulo_nunca_passa_de_noventa_graus():
    """Eco pode empurrar o pico alem do limite fisico; a resposta certa e
    'totalmente para o lado', nunca um arcsin invalido (NaN)."""
    loc = LocalizadorDirecao(TAXA, SEPARACAO)
    for atraso in (-20, 20):  # bem alem das ~9 amostras possiveis
        esquerda, direita = _par_com_atraso(atraso)
        r = loc.estimar(esquerda, direita)
        if r is not None:
            assert not np.isnan(r.angulo_graus)
            assert -90.0 <= r.angulo_graus <= 90.0


def test_separacao_maior_permite_atraso_maior():
    """A separacao fisica nao e ajuste fino: ela define a janela de busca."""
    estreito = LocalizadorDirecao(TAXA, 0.15)
    largo = LocalizadorDirecao(TAXA, 0.30)
    assert largo.atraso_maximo_amostras > estreito.atraso_maximo_amostras
    # 30cm/343m/s * 16000 = ~14 amostras
    assert 13.0 < largo.atraso_maximo_amostras < 15.0


def test_separacao_invalida_e_recusada():
    with pytest.raises(ValueError):
        LocalizadorDirecao(TAXA, 0.0)
