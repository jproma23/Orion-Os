"""Service Registry do Kernel (Cap 6 secao 6).

Cada modulo se registra com nome, versao, dependencias e servicos
oferecidos. O Kernel usa esse registro para descobrir modulos, checar se
as dependencias de um modulo ja estao rodando antes de inicia-lo, e
supervisiona-los junto com o Health Monitor / Watchdog.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EstadoModulo(str, Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPED = "STOPPED"


@dataclass
class ModuloRegistrado:
    nome: str
    versao: str
    dependencias: list[str] = field(default_factory=list)
    servicos: list[str] = field(default_factory=list)
    estado: EstadoModulo = EstadoModulo.STARTING


class ErroServiceRegistry(Exception):
    """Operacao invalida no Service Registry (modulo duplicado, inexistente, etc.)."""


class ServiceRegistry:
    """Registro central de modulos do sistema."""

    def __init__(self) -> None:
        self._modulos: dict[str, ModuloRegistrado] = {}

    def registrar(
        self,
        nome: str,
        versao: str,
        dependencias: list[str] | None = None,
        servicos: list[str] | None = None,
    ) -> ModuloRegistrado:
        """Registra um novo modulo com estado inicial STARTING."""
        if nome in self._modulos:
            raise ErroServiceRegistry(f"Modulo ja registrado: '{nome}'")
        modulo = ModuloRegistrado(
            nome=nome,
            versao=versao,
            dependencias=list(dependencias or []),
            servicos=list(servicos or []),
        )
        self._modulos[nome] = modulo
        return modulo

    def atualizar_estado(self, nome: str, estado: EstadoModulo) -> None:
        self._obter_ou_falhar(nome).estado = estado

    def obter(self, nome: str) -> ModuloRegistrado:
        return self._obter_ou_falhar(nome)

    def _obter_ou_falhar(self, nome: str) -> ModuloRegistrado:
        if nome not in self._modulos:
            raise ErroServiceRegistry(f"Modulo nao registrado: '{nome}'")
        return self._modulos[nome]

    def existe(self, nome: str) -> bool:
        return nome in self._modulos

    def todos(self) -> list[ModuloRegistrado]:
        return list(self._modulos.values())

    def dependencias_satisfeitas(self, nome: str) -> bool:
        """True se todas as dependencias do modulo estiverem RUNNING.

        Modulos que dependem de algo nunca registrado sao tratados como
        dependencia nao satisfeita (nao como erro) - o Boot Manager decide
        o que fazer (ex.: seguir em modo degradado, Cap 6 secao 8).
        """
        modulo = self._obter_ou_falhar(nome)
        for dependencia in modulo.dependencias:
            dep = self._modulos.get(dependencia)
            if dep is None or dep.estado is not EstadoModulo.RUNNING:
                return False
        return True

    def por_estado(self, estado: EstadoModulo) -> list[ModuloRegistrado]:
        return [m for m in self._modulos.values() if m.estado is estado]
