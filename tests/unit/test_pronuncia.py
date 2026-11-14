"""Acento de volta antes da voz (2026-11-14): "relacao" vira "relação"."""
import pytest

from orion.voice.pronuncia import acentuar


@pytest.mark.parametrize("entrada,saida", [
    # a frase que o dono ouviu errado em 14/11
    ("Relacao dono e Atom. Joao Paulo e o engenheiro, nao um usuario.",
     "Relação dono e Atom. João Paulo e o engenheiro, não um usuario."),
    ("A configuracao unica vive em orion.yaml", "A configuração única vive em orion.yaml"),
    ("Nao consigo enxergar agora: a visao deu erro.",
     "Não consigo enxergar agora: a visão deu erro."),
    ("acoes, botoes e maos", "ações, botões e mãos"),
    ("NAO", "NÃO"),
    ("Voce ja esta aqui tambem", "Você já esta aqui também"),
    ("o Fofao foi ate a cozinha", "o Fofão foi até a cozinha"),
    ("o cao latiu", "o cão latiu"),
])
def test_acentua_o_que_e_inequivoco(entrada, saida):
    assert acentuar(entrada) == saida


@pytest.mark.parametrize("texto", [
    "Vou ao mercado e volto aos poucos.",  # preposicao fica
    "Isso virou um caos.",
    "Relação, configuração, não, você, também já.",  # ja acentuado: igual
    "Paulo, Bruno, Marah",
    "ORION OS 2026 v1.0",
    "",
])
def test_nao_estraga_o_que_ja_esta_certo(texto):
    assert acentuar(texto) == texto
