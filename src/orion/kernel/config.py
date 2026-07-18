"""Configuration Manager do Kernel (Cap 6 secao 3; Cap 17).

Carrega `config/orion.yaml`, valida a presenca e o tipo dos campos que os
demais modulos do Kernel (Fase 1) dependem, e entrega o acesso as secoes.
A validacao aqui e propositalmente enxuta: cobre so o que a Fase 1 usa
(system, communication). Modulos futuros (vision, voice, ai, ...) podem
estender `_ESQUEMA` quando comecarem a depender de suas proprias secoes -
por enquanto o objetivo e falhar cedo e com mensagem clara (Cap 17 secao 2),
nao validar o arquivo inteiro de uma vez.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

NIVEIS_LOG_VALIDOS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})

# Esquema minimo: cada entrada e (caminho.pontilhado, tipo_esperado).
# Caminho pontilhado navega por dicionarios aninhados no YAML.
_ESQUEMA: tuple[tuple[str, type], ...] = (
    ("system.robot_name", str),
    ("system.log_level", str),
    ("system.profile", str),
    ("communication.raspberry.tcp_port", int),
    ("communication.arduino.baud_rate", int),
    ("communication.ack_timeout_ms", int),
    ("communication.max_retries", int),
    ("communication.heartbeat_interval_s", (int, float)),
    ("communication.heartbeats_lost_threshold", int),
)


class ErroConfiguracaoInvalida(Exception):
    """Configuracao ausente, malformada ou fora do esquema esperado."""


def _buscar_caminho(dados: dict[str, Any], caminho: str) -> Any:
    """Navega um dict aninhado usando um caminho tipo 'a.b.c'."""
    atual: Any = dados
    percorrido: list[str] = []
    for chave in caminho.split("."):
        percorrido.append(chave)
        if not isinstance(atual, dict) or chave not in atual:
            raise ErroConfiguracaoInvalida(
                f"Campo obrigatorio ausente em orion.yaml: '{'.'.join(percorrido)}'"
            )
        atual = atual[chave]
    return atual


class ConfigurationManager:
    """Carrega e expoe a configuracao unica do ORION OS (Cap 17)."""

    def __init__(self, caminho_yaml: Path | str = "config/orion.yaml") -> None:
        self._caminho = Path(caminho_yaml)
        self._dados: dict[str, Any] = {}

    @property
    def caminho(self) -> Path:
        return self._caminho

    def carregar(self) -> "ConfigurationManager":
        """Le o YAML do disco, faz o parse e valida contra o esquema minimo.

        Levanta ErroConfiguracaoInvalida com mensagem clara em qualquer
        problema (Cap 17 secao 2: "Configuracao invalida -> boot abortado").
        """
        if not self._caminho.exists():
            raise ErroConfiguracaoInvalida(
                f"Arquivo de configuracao nao encontrado: {self._caminho}"
            )

        try:
            texto = self._caminho.read_text(encoding="utf-8")
            dados = yaml.safe_load(texto)
        except yaml.YAMLError as erro:
            raise ErroConfiguracaoInvalida(f"YAML invalido em {self._caminho}: {erro}") from erro

        if not isinstance(dados, dict):
            raise ErroConfiguracaoInvalida(
                f"Conteudo de {self._caminho} nao e um mapeamento YAML valido."
            )

        self._dados = dados
        self._validar()
        return self

    def _validar(self) -> None:
        for caminho, tipo_esperado in _ESQUEMA:
            valor = _buscar_caminho(self._dados, caminho)
            if not isinstance(valor, tipo_esperado):
                raise ErroConfiguracaoInvalida(
                    f"Campo '{caminho}' deveria ser {tipo_esperado}, "
                    f"recebeu {type(valor).__name__} ({valor!r})"
                )

        nivel = self._dados["system"]["log_level"]
        if nivel not in NIVEIS_LOG_VALIDOS:
            raise ErroConfiguracaoInvalida(
                f"system.log_level invalido: {nivel!r}. "
                f"Use um de {sorted(NIVEIS_LOG_VALIDOS)}."
            )

    def secao(self, nome: str) -> dict[str, Any]:
        """Retorna a secao de configuracao pedida (ex.: 'motion', 'vision').

        Cada modulo deve receber apenas sua propria secao (Cap 17 secao 5),
        nunca o dict inteiro - assim nenhum modulo fica acoplado a campos
        de outro dominio.
        """
        if nome not in self._dados:
            raise ErroConfiguracaoInvalida(f"Secao de configuracao inexistente: '{nome}'")
        return self._dados[nome]

    def bruto(self) -> dict[str, Any]:
        """Retorna uma copia do dict completo - uso restrito (ex.: Boot Manager)."""
        return dict(self._dados)
