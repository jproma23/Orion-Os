"""Boot Manager do Kernel (Cap 6 secao 4).

Orquestra a sequencia de inicializacao do ORION OS. Etapas que dependem de
hardware ou de fases futuras do projeto (deteccao do Raspberry/Arduino,
banco de dados, IA, Vision, Motion Core) ainda nao existem neste ponto
(Fase 1) - o Boot Manager tolera modulos ausentes: registra o que falta e
segue em frente. So aborta se a propria configuracao for invalida
(Cap 17 secao 2).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from orion.kernel.config import ConfigurationManager
from orion.kernel.event_bus import EventBus, Prioridade
from orion.kernel.logger import configurar_logger
from orion.kernel.registry import EstadoModulo, ServiceRegistry
from orion.kernel.watchdog import HealthMonitor, Watchdog

if TYPE_CHECKING:  # so para type hints - ver import local mais abaixo
    from orion.communication.service import ComunicacaoService

VERSAO_KERNEL = "0.1.0"

#: tempo maximo esperando a conexao TCP com o Raspberry antes de desistir
#: e tolerar a ausencia (Cap 6 s.8) - sem isso, um Raspberry desligado ou
#: fora do alcance faria o boot travar esperando o timeout do proprio SO.
TIMEOUT_CONEXAO_RASPBERRY_S = 3.0

# Etapas do Cap 6 secao 4 que dependem de hardware/fases ainda nao
# implementadas neste projeto. Cada uma vira um evento diagnostic
# informativo no boot, em vez de travar a inicializacao. "raspberry" saiu
# desta lista - agora e uma tentativa de conexao de verdade (ver
# _conectar_raspberry), nao so um aviso.
_ETAPAS_PENDENTES = (
    ("arduino", "Deteccao do Arduino via Raspberry (Fase 2/4)"),
    ("database", "Banco de dados no SSD (Fase 3)"),
    ("ai", "IA / Ollama (Fase 6)"),
    ("vision", "Vision Core (Fase 5)"),
    ("motion_hardware", "Motion Core + Hardware Core (Fase 4/7)"),
)


async def _conectar_raspberry(
    comm: ComunicacaoService,
    config: ConfigurationManager,
    event_bus: EventBus,
    logger: logging.Logger,
) -> bool:
    """Tenta conectar no Motion Core (Raspberry) via TCP (Cap 14 s.2) e
    confirma via WHO_ARE_YOU. Tolera ausencia (Cap 6 s.8): Raspberry
    desligado ou fora da rede nao aborta o boot do Notebook."""
    # import local, nao no topo do arquivo: orion.communication.* importa
    # orion.kernel.event_bus, e orion/kernel/__init__.py importa boot.py
    # de olhos fechados antes de mais nada - import no topo daria um
    # ciclo de import (achado real, quebrava toda importacao do pacote
    # orion.kernel).
    from orion.communication.discovery import ErroVersaoIncompativel, descobrir
    from orion.communication.service import ErroComunicacao
    from orion.communication.transport import ErroTransporte, TcpTransport

    conf_raspberry = config.secao("communication")["raspberry"]
    cliente = TcpTransport(conf_raspberry["host"], conf_raspberry["tcp_port"])
    try:
        await asyncio.wait_for(cliente.conectar(), timeout=TIMEOUT_CONEXAO_RASPBERRY_S)
    except (ErroTransporte, asyncio.TimeoutError) as erro:
        logger.warning(
            "Raspberry (Motion Core) inalcancavel em %s:%d: %s",
            conf_raspberry["host"],
            conf_raspberry["tcp_port"],
            erro,
        )
        await event_bus.publish(
            "diagnostic.error",
            {"modulo": "raspberry", "motivo": "inalcancavel", "detalhe": str(erro)},
            prioridade=Prioridade.BAIXA,
        )
        return False

    comm.adicionar_link("motion_core", cliente)
    try:
        info = await descobrir(comm, "motion_core", event_bus, timeout_s=TIMEOUT_CONEXAO_RASPBERRY_S)
    except (ErroVersaoIncompativel, ErroComunicacao) as erro:
        logger.warning("Raspberry conectou mas nao respondeu WHO_ARE_YOU: %s", erro)
        await event_bus.publish(
            "diagnostic.error",
            {"modulo": "raspberry", "motivo": "sem_resposta_who_are_you", "detalhe": str(erro)},
            prioridade=Prioridade.BAIXA,
        )
        return False

    logger.info("Raspberry (Motion Core) conectado: %s v%s", info.nome, info.versao_modulo)
    return True


class SistemaOrion:
    """Handles vivos apos o boot - usados pelo chamador e no encerramento."""

    def __init__(
        self,
        config: ConfigurationManager,
        event_bus: EventBus,
        registry: ServiceRegistry,
        health_monitor: HealthMonitor,
        watchdog: Watchdog,
        logger: logging.Logger,
        tarefa_bus: asyncio.Task,
        tarefa_watchdog: asyncio.Task,
        comm: ComunicacaoService,
        raspberry_conectado: bool,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.registry = registry
        self.health_monitor = health_monitor
        self.watchdog = watchdog
        self.logger = logger
        self.comm = comm
        self.raspberry_conectado = raspberry_conectado
        self._tarefa_bus = tarefa_bus
        self._tarefa_watchdog = tarefa_watchdog

    async def encerrar(self) -> None:
        """Desligamento seguro: para o watchdog e o event bus (Cap 6)."""
        self.logger.info("Iniciando desligamento seguro do ORION OS")
        self.watchdog.parar()
        await self.comm.encerrar()
        self.event_bus.parar()
        for tarefa in (self._tarefa_watchdog, self._tarefa_bus):
            tarefa.cancel()
            try:
                await tarefa
            except asyncio.CancelledError:
                pass
        self.registry.atualizar_estado("mission_core", EstadoModulo.STOPPED)
        self.logger.info("ORION OS encerrado")


class BootManager:
    """Executa a sequencia de boot do Cap 6 secao 4."""

    def __init__(
        self,
        caminho_config: Path | str = "config/orion.yaml",
        caminho_log: Path | str = "data/logs/orion.log",
        simulado: bool = False,
    ) -> None:
        self._caminho_config = caminho_config
        self._caminho_log = caminho_log
        self._simulado = simulado

    async def iniciar(self) -> SistemaOrion:
        # 1. Carregar configuracao (aborta aqui se invalida - Cap 17 secao 2).
        config = ConfigurationManager(self._caminho_config).carregar()
        secao_sistema = config.secao("system")

        # 2. Inicializar Logger.
        logger = configurar_logger(
            "orion", nivel=secao_sistema["log_level"], arquivo_log=self._caminho_log
        )
        modo = "SIMULADO" if self._simulado else "REAL"
        logger.info("Boot iniciado - robot_name=%s modo=%s", secao_sistema["robot_name"], modo)

        # 3. Inicializar Event Bus.
        event_bus = EventBus()
        tarefa_bus = asyncio.create_task(event_bus.iniciar())

        # 4. Registrar Mission Core (este processo).
        registry = ServiceRegistry()
        registry.registrar("mission_core", VERSAO_KERNEL, servicos=["kernel"])
        registry.atualizar_estado("mission_core", EstadoModulo.RUNNING)

        # 5. Comunicacao (Cap 14) + deteccao do Raspberry Pi (Fase 2) - agora
        # implementada de verdade: tenta conectar via TCP e tolera ausencia
        # (Cap 6 secao 8) em vez de so logar "nao implementado". Import
        # local - ver comentario em _conectar_raspberry sobre o ciclo de
        # import evitado.
        from orion.communication.service import ComunicacaoService

        conf_comm = config.secao("communication")
        comm = ComunicacaoService(
            "mission_core",
            event_bus,
            max_retries=conf_comm["max_retries"],
            ack_timeout_ms=conf_comm["ack_timeout_ms"],
            versao_modulo=VERSAO_KERNEL,
        )
        raspberry_conectado = await _conectar_raspberry(comm, config, event_bus, logger)

        # 6-10. Arduino (via Raspberry), banco, IA, Vision, Motion Core +
        # Hardware Core: ainda nao implementados nesta fase do projeto.
        # Tolerar modulo ausente em vez de abortar o boot (Cap 6 secao 8).
        for chave, descricao in _ETAPAS_PENDENTES:
            logger.info("Etapa de boot ainda nao implementada: %s", descricao)
            await event_bus.publish(
                "diagnostic.error",
                {"modulo": chave, "motivo": "nao_implementado", "descricao": descricao},
                prioridade=Prioridade.BAIXA,
            )

        # Health Monitor + Watchdog: prontos para monitorar modulos reais
        # assim que as fases seguintes os registrarem.
        health_monitor = HealthMonitor(
            intervalo_heartbeat_s=conf_comm["heartbeat_interval_s"],
            heartbeats_perdidos_limite=conf_comm["heartbeats_lost_threshold"],
        )
        watchdog = Watchdog(health_monitor, event_bus=event_bus)
        tarefa_watchdog = asyncio.create_task(watchdog.iniciar())

        # 11. Autotestes (minimo nesta fase: bus e registry respondendo).
        assert registry.existe("mission_core")
        logger.info("Autotestes basicos do Kernel: OK")

        # 12. Publicar system.ready.
        await event_bus.publish(
            "system.ready",
            {"robot_name": secao_sistema["robot_name"], "modo": modo},
            prioridade=Prioridade.CRITICA,
        )
        await event_bus.aguardar_fila_vazia()
        logger.info("system.ready publicado - boot concluido")

        return SistemaOrion(
            config=config,
            event_bus=event_bus,
            registry=registry,
            health_monitor=health_monitor,
            watchdog=watchdog,
            logger=logger,
            tarefa_bus=tarefa_bus,
            tarefa_watchdog=tarefa_watchdog,
            comm=comm,
            raspberry_conectado=raspberry_conectado,
        )
