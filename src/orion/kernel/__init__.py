"""Kernel do ORION OS (Cap 6): Boot Manager, Event Bus, Service Registry,
Health Monitor + Watchdog, Configuration Manager, Logger.
"""
from orion.kernel.boot import BootManager, SistemaOrion
from orion.kernel.config import ConfigurationManager, ErroConfiguracaoInvalida
from orion.kernel.event_bus import Evento, EventBus, Prioridade
from orion.kernel.registry import EstadoModulo, ModuloRegistrado, ServiceRegistry
from orion.kernel.watchdog import HealthMonitor, Watchdog

__all__ = [
    "BootManager",
    "SistemaOrion",
    "ConfigurationManager",
    "ErroConfiguracaoInvalida",
    "Evento",
    "EventBus",
    "Prioridade",
    "EstadoModulo",
    "ModuloRegistrado",
    "ServiceRegistry",
    "HealthMonitor",
    "Watchdog",
]
