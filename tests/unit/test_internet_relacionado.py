"""Teste do portao de pertinencia da busca (2026-08-07).

Todos os casos abaixo foram MEDIDOS em uso real, com o robo respondendo -
nao sao hipoteses. A busca por titulo da Wikipedia sempre devolve o menos
ruim, e num termo que ela nao conhece isso e qualquer coisa.
"""
import pytest

from orion.mission.internet import parece_relacionado


@pytest.mark.parametrize(
    "termo,titulo",
    [
        ("odiando", "Odido"),
        ("estatico", "Estádio do Morumbi"),
        ("dinamico", "Dinamite"),
        ("O que é estar vivo", "Organização das Nações Unidas"),
    ],
)
def test_recusa_o_que_nao_tem_relacao(termo, titulo):
    assert not parece_relacionado(termo, titulo)


@pytest.mark.parametrize(
    "termo,titulo",
    [
        ("celula", "Célula"),          # acento nao pode separar os dois
        ("fotossintese", "Fotossíntese"),
        ("velocidade", "Velocidade"),
        ("equacao do segundo grau", "Equação quadrática"),
        ("cinematica", "Cinemática"),
    ],
)
def test_aceita_o_que_tem_relacao(termo, titulo):
    assert parece_relacionado(termo, titulo)


def test_limite_conhecido_termo_generico_ainda_passa():
    """O que esta funcao NAO resolve, escrito para nao se esquecer.

    "graficos" numa aula de cinematica trouxe "Graficos isometricos em
    videojogos" - e o titulo DIVIDE a palavra procurada, entao nenhuma
    comparacao por palavra pode recusar isso. Termo generico precisa de
    contexto, e contexto nao esta neste nivel.

    Este teste existe para o dia em que alguem achar que o portao resolve
    tudo: ele nao resolve, e o caminho seguro continua sendo o rascunho
    lido por gente antes de virar aula.
    """
    assert parece_relacionado("graficos", "Gráficos isométricos em videojogos")
    # "cor do ceu" x "Cor dos cabelos" divide a palavra "cor" - mesmo caso.
    assert parece_relacionado("cor do ceu", "Cor dos cabelos")


def test_termo_so_de_palavras_curtas_passa():
    """Recusar tudo aqui deixaria o robo mudo por excesso de zelo."""
    assert parece_relacionado("o que e um mol", "Mol")
