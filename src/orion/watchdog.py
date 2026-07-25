"""Health Monitor + Watchdog do Kernel (Cap 6 secao 8).

O Health Monitor rastreia o ultimo heartbeat de cada modulo registrado.
Quando um modulo para de enviar heartbeat, o Watchdog escala em ordem:
1. tenta reconectar (se o modulo fornecer um callback de reconexao);
2. reinicia o modulo (se fornecer um callback de reinicio);
3. registra em log;
4. publica um evento (diagnostic.error) para a interface ser notificada.

Cada modulo e tratado isoladamente - a falha de um nao reinicia o sistema
inteiro (regra do Cap 6).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from orion.kernel.event_bus import EventBus, Prioridade

logger = logging.getLogger("orion.kernel.watchdog")

CallbackRecuperacao = Callable[[], "Awaitable[Any] | Any"]


@dataclass
class _EstadoModuloMonitorado:
    ultimo_heartbeat: float
    reconectar: CallbackRecuperacao | None = None
    reiniciar: CallbackRecuperacao | None = None
    tentativas_reconexao: int = 0
    #: Limite proprio deste modulo; None = usa o limite global do monitor.
    #: Existe porque nem todo enlace merece a mesma paciencia - ver
    #: HealthMonitor.timeout_de.
    limite_proprio: int | None = None


class HealthMonitor:
    """Rastreia heartbeats de modulos registrados."""

    def __init__(self, intervalo_heartbeat_s: float, heartbeats_perdidos_limite: int) -> None:
        if intervalo_heartbeat_s <= 0:
            raise ValueError("intervalo_heartbeat_s deve ser positivo")
        if heartbeats_perdidos_limite <= 0:
            raise ValueError("heartbeats_perdidos_limite deve ser positivo")
        self._intervalo = intervalo_heartbeat_s
        self._limite = heartbeats_perdidos_limite
        self._modulos: dict[str, _EstadoModuloMonitorado] = {}

    @property
    def timeout_s(self) -> float:
        """Timeout PADRAO, usado por quem nao tem limite proprio."""
        return self._intervalo * self._limite

    def timeout_de(self, nome: str) -> float:
        """Timeout deste modulo especifico.

        Enlaces diferentes merecem paciencia diferente. O link com o
        Arduino precisa de deteccao rapida (e o caminho da seguranca
        reativa); o link com o Notebook precisa TOLERAR pausas longas,
        porque uma inferencia de IA local satura a CPU da maquina por
        dezenas de segundos e atrasa os heartbeats sem que nada esteja
        quebrado - com limite curto, o Pi declarava o link morto a cada
        consulta a IA (medido em 2026-07-19).
        """
        estado = self._modulos.get(nome)
        if estado is None or estado.limite_proprio is None:
            return self.timeout_s
        return self._intervalo * estado.limite_proprio

    def registrar_modulo(
        self,
        nome: str,
        reconectar: CallbackRecuperacao | None = None,
        reiniciar: CallbackRecuperacao | None = None,
        agora: float | None = None,
        heartbeats_perdidos_limite: int | None = None,
    ) -> None:
        """`heartbeats_perdidos_limite` sobrescreve o limite global so para
        este modulo (None = herda o global). Ver timeout_de."""
        if heartbeats_perdidos_limite is not None and heartbeats_perdidos_limite <= 0:
            raise ValueError("heartbeats_perdidos_limite deve ser positivo")
        self._modulos[nome] = _EstadoModuloMonitorado(
            ultimo_heartbeat=agora if agora is not None else time.monotonic(),
            reconectar=reconectar,
            reiniciar=reiniciar,
            limite_proprio=heartbeats_perdidos_limite,
        )

    def receber_heartbeat(self, nome: str, agora: float | None = None) -> None:
        if nome not in self._modulos:
            raise KeyError(f"Modulo nao monitorado: '{nome}'")
        estado = self._modulos[nome]
        estado.ultimo_heartbeat = agora if agora is not None else time.monotonic()
        estado.tentativas_reconexao = 0

    def modulos_com_heartbeat_perdido(self, agora: float | None = None) -> list[str]:
        instante = agora if agora is not None else time.monotonic()
        return [
            nome
            for nome, estado in self._modulos.items()
            if instante - estado.ultimo_heartbeat > self.timeout_de(nome)
        ]

    def estado_de(self, nome: str) -> _EstadoModuloMonitorado:
        return self._modulos[nome]


class Watchdog:
    """Escalona a recuperacao de modulos que perderam heartbeat."""

    def __init__(self, health_monitor: HealthMonitor, event_bus: EventBus | None = None) -> None:
        self._health_monitor = health_monitor
        self._event_bus = event_bus
        self._executando = False

    async def verificar_uma_vez(self, agora: float | None = None) -> list[str]:
        """Roda uma checagem e escala a recuperacao dos modulos perdidos.

        Retorna a lista de modulos que estavam com heartbeat perdido nesta
        checagem - util em testes, sem precisar rodar o loop continuo.
        `agora` permite forcar o instante de referencia nos testes.
        """
        perdidos = self._health_monitor.modulos_com_heartbeat_perdido(agora=agora)
        for nome in perdidos:
            await self._escalar(nome)
        return perdidos

    async def _escalar(self, nome: str) -> None:
        estado = self._health_monitor.estado_de(nome)
        estado.tentativas_reconexao += 1

        logger.warning(
            "Heartbeat perdido: modulo='%s' tentativa=%d", nome, estado.tentativas_reconexao
        )

        recuperado = False
        if estado.reconectar is not None:
            recuperado = (await self._chamar(estado.reconectar)) is not False

        if not recuperado and estado.reiniciar is not None:
            logger.warning("Reconexao falhou, reiniciando modulo='%s'", nome)
            await self._chamar(estado.reiniciar)

        if self._event_bus is not None:
            await self._event_bus.publish(
                "diagnostic.error",
                {
                    "modulo": nome,
                    "motivo": "heartbeat_perdido",
                    "tentativa": estado.tentativas_reconexao,
                },
                prioridade=Prioridade.ALTA,
            )

    @staticmethod
    async def _chamar(callback: CallbackRecuperacao) -> Any:
        """Chama o callback (sync ou async); excecao vira log + falha, nunca crash."""
        try:
            resultado = callback()
            if asyncio.iscoroutine(resultado):
                resultado = await resultado
            return resultado
        except Exception:
            logger.exception("Callback de recuperacao falhou")
            return False

    async def iniciar(self, intervalo_verificacao_s: float = 1.0) -> None:
        """Loop continuo: verifica heartbeats perdidos a cada `intervalo_verificacao_s`."""
        self._executando = True
        while self._executando:
            await self.verificar_uma_vez()
            await asyncio.sleep(intervalo_verificacao_s)

    def parar(self) -> None:
        self._executando = False
