"""Testes do Service Registry (Cap 6 secao 6)."""
import pytest

from orion.kernel.registry import EstadoModulo, ErroServiceRegistry, ServiceRegistry


def test_registrar_comeca_em_starting():
    registry = ServiceRegistry()
    modulo = registry.registrar("vision", "0.1.0")
    assert modulo.estado is EstadoModulo.STARTING


def test_registrar_duplicado_falha():
    registry = ServiceRegistry()
    registry.registrar("vision", "0.1.0")
    with pytest.raises(ErroServiceRegistry):
        registry.registrar("vision", "0.2.0")


def test_obter_modulo_inexistente_falha():
    registry = ServiceRegistry()
    with pytest.raises(ErroServiceRegistry):
        registry.obter("inexistente")


def test_atualizar_estado():
    registry = ServiceRegistry()
    registry.registrar("vision", "0.1.0")
    registry.atualizar_estado("vision", EstadoModulo.RUNNING)
    assert registry.obter("vision").estado is EstadoModulo.RUNNING


def test_dependencias_satisfeitas_quando_dependencia_esta_rodando():
    registry = ServiceRegistry()
    registry.registrar("mission_core", "0.1.0")
    registry.atualizar_estado("mission_core", EstadoModulo.RUNNING)
    registry.registrar("vision", "0.1.0", dependencias=["mission_core"])

    assert registry.dependencias_satisfeitas("vision") is True


def test_dependencias_nao_satisfeitas_quando_dependencia_nao_esta_rodando():
    registry = ServiceRegistry()
    registry.registrar("mission_core", "0.1.0")  # ainda em STARTING
    registry.registrar("vision", "0.1.0", dependencias=["mission_core"])

    assert registry.dependencias_satisfeitas("vision") is False


def test_dependencias_nao_satisfeitas_quando_dependencia_nunca_registrada():
    registry = ServiceRegistry()
    registry.registrar("vision", "0.1.0", dependencias=["modulo_fantasma"])

    assert registry.dependencias_satisfeitas("vision") is False


def test_por_estado():
    registry = ServiceRegistry()
    registry.registrar("vision", "0.1.0")
    registry.registrar("voice", "0.1.0")
    registry.atualizar_estado("voice", EstadoModulo.RUNNING)

    assert [m.nome for m in registry.por_estado(EstadoModulo.RUNNING)] == ["voice"]
    assert [m.nome for m in registry.por_estado(EstadoModulo.STARTING)] == ["vision"]
