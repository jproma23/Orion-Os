"""Testes do Configuration Manager (Cap 17)."""
from pathlib import Path

import pytest
import yaml

from orion.kernel.config import ConfigurationManager, ErroConfiguracaoInvalida

CONFIG_MINIMA_VALIDA = {
    "system": {"robot_name": "Jarvis", "log_level": "INFO", "profile": "HOME"},
    "communication": {
        "raspberry": {"host": "10.20.20.196", "tcp_port": 5757},
        "hardware_core": {"baud_rate": 115200},
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

    assert config.secao("system")["robot_name"] == "Jarvis"


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
        "system": {"robot_name": "Jarvis", "log_level": "INFO", "profile": "HOME"},
        "communication": {
            "raspberry": {"host": "10.20.20.196", "tcp_port": "5757"},  # deveria ser int
            "hardware_core": {"baud_rate": 115200},
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


def test_raspberry_host_ausente_falha(tmp_path):
    """Achado real da vistoria de 2026-07-24: sem essa validacao,
    communication.raspberry.host podia faltar do orion.yaml e a config
    passava normal - so quebrava depois com KeyError cru la no
    kernel/boot.py, antes mesmo do try/except que deveria tolerar
    "Raspberry desligado"."""
    dados = {k: dict(v) if isinstance(v, dict) else v for k, v in CONFIG_MINIMA_VALIDA.items()}
    dados["communication"] = dict(dados["communication"])
    dados["communication"]["raspberry"] = {"tcp_port": 5757}  # falta "host"
    caminho = _escrever_config(tmp_path, dados)

    with pytest.raises(ErroConfiguracaoInvalida, match="host"):
        ConfigurationManager(caminho).carregar()


def test_secao_inexistente_falha(tmp_path):
    caminho = _escrever_config(tmp_path, CONFIG_MINIMA_VALIDA)
    config = ConfigurationManager(caminho).carregar()

    with pytest.raises(ErroConfiguracaoInvalida):
        config.secao("secao_fantasma")


def test_arquivo_local_sobrepoe_so_o_que_declara(tmp_path):
    """O orion.local.yaml de cada maquina troca so as chaves que declara, e
    entra nos dicionarios aninhados em vez de substituir a secao inteira."""
    caminho = _escrever_config(tmp_path, CONFIG_MINIMA_VALIDA)
    (tmp_path / "orion.local.yaml").write_text(
        yaml.safe_dump({"communication": {"raspberry": {"host": "pi-os.local"}}}),
        encoding="utf-8",
    )

    config = ConfigurationManager(caminho).carregar()

    raspberry = config.secao("communication")["raspberry"]
    assert raspberry["host"] == "pi-os.local"
    assert raspberry["tcp_port"] == CONFIG_MINIMA_VALIDA["communication"]["raspberry"]["tcp_port"]


def test_sem_arquivo_local_vale_o_orion_yaml(tmp_path):
    caminho = _escrever_config(tmp_path, CONFIG_MINIMA_VALIDA)
    config = ConfigurationManager(caminho).carregar()
    assert config.secao("communication")["raspberry"]["host"] == (
        CONFIG_MINIMA_VALIDA["communication"]["raspberry"]["host"]
    )


def test_arquivo_local_ainda_passa_pela_validacao(tmp_path):
    """Sobrepor nao pode virar um jeito de entrar com config quebrada."""
    caminho = _escrever_config(tmp_path, CONFIG_MINIMA_VALIDA)
    (tmp_path / "orion.local.yaml").write_text(
        yaml.safe_dump({"communication": {"raspberry": {"tcp_port": "5757"}}}),
        encoding="utf-8",
    )
    with pytest.raises(ErroConfiguracaoInvalida):
        ConfigurationManager(caminho).carregar()
