"""Testes do Configuration Manager (Cap 17)."""
from pathlib import Path

import pytest
import yaml

from orion.kernel.config import ConfigurationManager, ErroConfiguracaoInvalida

CONFIG_MINIMA_VALIDA = {
    "system": {"robot_name": "Fofão", "log_level": "INFO", "profile": "HOME"},
    "communication": {
        "raspberry": {"tcp_port": 5757},
        "arduino": {"baud_rate": 115200},
        "ack_timeout_ms": 500,
        "max_retries": 3,
        "heartbeat_interval_s": 1.0,
        "heartbeats_lost_threshold": 3,
    },
}


def _escrever_config(tmp_path: Path, dados: dict) -> Path:
    caminho = tmp_path / "orion.yaml"
    caminho.write_text(yaml.safe_dump(dados), encoding="utf-8")
    return caminho


def test_carregar_config_valida(tmp_path):
    caminho = _escrever_config(tmp_path, CONFIG_MINIMA_VALIDA)
    config = ConfigurationManager(caminho).carregar()

    assert config.secao("system")["robot_name"] == "Fofão"


def test_arquivo_do_projeto_e_valido():
    """A config real do repo (config/orion.yaml) deve passar na validacao."""
    config = ConfigurationManager("config/orion.yaml").carregar()
    assert config.secao("system")["robot_name"]


def test_arquivo_inexistente_falha():
    with pytest.raises(ErroConfiguracaoInvalida):
        ConfigurationManager("config/nao_existe.yaml").carregar()


def test_yaml_invalido_falha(tmp_path):
    caminho = tmp_path / "orion.yaml"
    caminho.write_text("system: [nao fecha a lista", encoding="utf-8")
    with pytest.raises(ErroConfiguracaoInvalida):
        ConfigurationManager(caminho).carregar()


def test_campo_obrigatorio_ausente_falha(tmp_path):
    dados = {k: v for k, v in CONFIG_MINIMA_VALIDA.items()}
    dados["system"] = {"log_level": "INFO"}  # falta robot_name
    caminho = _escrever_config(tmp_path, dados)

    with pytest.raises(ErroConfiguracaoInvalida, match="robot_name"):
        ConfigurationManager(caminho).carregar()


def test_tipo_incorreto_falha(tmp_path):
    dados = {
        "system": {"robot_name": "Fofão", "log_level": "INFO", "profile": "HOME"},
        "communication": {
            "raspberry": {"tcp_port": "5757"},  # deveria ser int
            "arduino": {"baud_rate": 115200},
            "ack_timeout_ms": 500,
            "max_retries": 3,
            "heartbeat_interval_s": 1.0,
            "heartbeats_lost_threshold": 3,
        },
    }
    caminho = _escrever_config(tmp_path, dados)

    with pytest.raises(ErroConfiguracaoInvalida, match="tcp_port"):
        ConfigurationManager(caminho).carregar()


def test_log_level_invalido_falha(tmp_path):
    dados = {k: dict(v) for k, v in CONFIG_MINIMA_VALIDA.items()}
    dados["system"] = dict(dados["system"], log_level="VERBOSE")
    caminho = _escrever_config(tmp_path, dados)

    with pytest.raises(ErroConfiguracaoInvalida, match="log_level"):
        ConfigurationManager(caminho).carregar()


def test_secao_inexistente_falha(tmp_path):
    caminho = _escrever_config(tmp_path, CONFIG_MINIMA_VALIDA)
    config = ConfigurationManager(caminho).carregar()

    with pytest.raises(ErroConfiguracaoInvalida):
        config.secao("secao_fantasma")
