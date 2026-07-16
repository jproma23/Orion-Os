"""Teste de fumaça da Fase 0: o pacote importa e expõe versão."""
from orion.__main__ import VERSAO


def test_versao_definida():
    assert VERSAO
