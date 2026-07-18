"""Motion Core — processo principal do Raspberry Pi (Cap 6, 12, 13, 14).

Ponto de entrada real do Motion Core, equivalente ao `python -m orion` do
lado do Notebook (Mission Core): sobe o servidor TCP que o Notebook
conecta, a ponte serial com o Arduino (Hardware Core), os módulos de
navegação/fusão de sensores (Cap 12) e a interface web (Cap 13 s.4) -
tudo compartilhando um único Event Bus.

Tolera módulos/dispositivos ausentes em vez de abortar (Cap 6 s.8): sem
Arduino conectado, sem SSD montado ou sem o Notebook plugado, o processo
sobe do mesmo jeito e cada parte que depende do que falta fica desativada
(logada como diagnostic.error), igual ao Boot Manager do Kernel já faz.

Uso:
    python -m motion_core
"""
from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from motion_core.memory.api import MemoryAPI
from motion_core.memory.database import DatabaseManager
from motion_core.navigation.fusao_sensores import FusaoSensores
from motion_core.navigation.navigation_core import NavigationCore
from motion_core.webui.server import WebUIServer
from orion.communication.discovery import ErroVersaoIncompativel, descobrir
from orion.communication.heartbeat import MonitorHeartbeat
from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.communication.transport import (
    ConexaoTcp,
    ErroTransporte,
    SerialTransport,
    iniciar_servidor_tcp,
)
from orion.kernel.config import ConfigurationManager
from orion.kernel.event_bus import EventBus, Prioridade
from orion.kernel.logger import configurar_logger
from orion.kernel.registry import EstadoModulo, ServiceRegistry

VERSAO_MOTION_CORE = "0.1.0"
NOME_MODULO = "motion_core"

#: "auto" em communication.arduino.port (Cap 17) significa "descoberta via
#: WHO_ARE_YOU no Raspberry" (comentario do proprio config/orion.yaml), nao
#: uma varredura de multiplas portas - esta e a porta real confirmada
#: nesta montagem (adaptador CH340, ver docs/journal.md Fase 2).
PORTA_SERIAL_PADRAO = "/dev/ttyUSB0"


async def _conectar_arduino(
    comm: ComunicacaoService,
    config_arduino: dict,
    event_bus: EventBus,
    logger: logging.Logger,
) -> bool:
    """Abre o link serial com o Hardware Core e confirma via WHO_ARE_YOU.
    Retorna False (tolerado, Cap 6 s.8) se a porta nao abrir ou o Arduino
    nao responder - nunca levanta excecao."""
    porta = config_arduino["port"]
    if porta == "auto":
        porta = PORTA_SERIAL_PADRAO

    transporte = SerialTransport(porta, baud_rate=config_arduino["baud_rate"])
    try:
        await transporte.conectar()
    except ErroTransporte as erro:
        logger.warning("Porta serial do Arduino indisponivel (%s): %s", porta, erro)
        await event_bus.publish(
            "diagnostic.error",
            {"modulo": "hardware_core", "motivo": "serial_indisponivel", "detalhe": str(erro)},
            prioridade=Prioridade.ALTA,
        )
        return False

    comm.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)
    try:
        await descobrir(comm, "hardware_core", event_bus, timeout_s=3.0)
    except (ErroVersaoIncompativel, ErroComunicacao) as erro:
        logger.warning("Arduino nao respondeu WHO_ARE_YOU em '%s': %s", porta, erro)
        await event_bus.publish(
            "diagnostic.error",
            {"modulo": "hardware_core", "motivo": "sem_resposta_who_are_you", "detalhe": str(erro)},
            prioridade=Prioridade.ALTA,
        )
        return False

    logger.info("Arduino (Hardware Core) conectado e confirmado em %s", porta)
    return True


def _abrir_memoria(config: ConfigurationManager, event_bus: EventBus, logger: logging.Logger):
    """Abre o banco de dados (Fase 3) se o SSD de producao estiver montado
    (Cap 15). Tolera ausencia - so a pagina CONVERSA fica sem historico."""
    conf_db = config.secao("database")
    caminho_db = Path(conf_db["path"])
    if not caminho_db.parent.exists():
        logger.warning(
            "SSD de producao nao montado (%s) - banco de dados indisponivel, "
            "pagina CONVERSA da interface web ficara so com o aviso",
            caminho_db.parent,
        )
        return None
    try:
        db = DatabaseManager(conf_db["path"], conf_db["backup_dir"])
        db.iniciar()
    except Exception:
        logger.exception("Falha ao abrir o banco de dados em %s", conf_db["path"])
        return None
    logger.info("Banco de dados conectado em %s", conf_db["path"])
    return MemoryAPI(db, event_bus)


async def principal() -> None:
    config = ConfigurationManager("config/orion.yaml").carregar()
    secao_sistema = config.secao("system")
    logger = configurar_logger(NOME_MODULO, nivel=secao_sistema["log_level"])
    logger.info("Motion Core: boot iniciado - robot_name=%s", secao_sistema["robot_name"])

    event_bus = EventBus()
    tarefa_bus = asyncio.create_task(event_bus.iniciar())

    registry = ServiceRegistry()
    registry.registrar(NOME_MODULO, VERSAO_MOTION_CORE, servicos=["navigation", "webui"])
    registry.atualizar_estado(NOME_MODULO, EstadoModulo.STARTING)

    conf_comm = config.secao("communication")
    comm = ComunicacaoService(
        NOME_MODULO,
        event_bus,
        max_retries=conf_comm["max_retries"],
        ack_timeout_ms=conf_comm["ack_timeout_ms"],
        versao_modulo=VERSAO_MOTION_CORE,
    )
    heartbeat = MonitorHeartbeat(
        comm,
        event_bus,
        intervalo_s=conf_comm["heartbeat_interval_s"],
        heartbeats_perdidos_limite=conf_comm["heartbeats_lost_threshold"],
    )

    # 1. Servidor TCP para o Notebook (Mission Core) conectar (Cap 14 s.2).
    conf_raspberry = conf_comm["raspberry"]

    async def _ao_conectar_notebook(conexao: ConexaoTcp) -> None:
        comm.adicionar_link("mission_core", conexao)
        heartbeat.monitorar("mission_core")
        logger.info("Notebook (Mission Core) conectado via TCP")

    servidor_tcp = await iniciar_servidor_tcp(
        "0.0.0.0", conf_raspberry["tcp_port"], _ao_conectar_notebook
    )
    logger.info(
        "Servidor TCP ouvindo em 0.0.0.0:%d - esperando o Notebook conectar",
        conf_raspberry["tcp_port"],
    )

    # 2. Ponte serial com o Arduino (Cap 14 s.2) - tolera ausencia.
    arduino_conectado = await _conectar_arduino(comm, conf_comm["arduino"], event_bus, logger)
    if arduino_conectado:
        heartbeat.monitorar("hardware_core")

    tarefa_heartbeat = asyncio.create_task(heartbeat.iniciar())

    # 3. Navegacao + Fusao de Sensores (Cap 12) - registradas mesmo sem
    # Arduino conectado; comandos so falhariam com ErroComunicacao na hora
    # de enviar, tratado normalmente pelo _ao_receber_comando existente.
    config_motion = config.secao("motion")
    config_navigation = config.secao("navigation")
    NavigationCore(event_bus, comm, config_motion, config_navigation)
    FusaoSensores(event_bus, config_motion)

    # 4. Memoria (Fase 3) - opcional, so se o SSD de producao existir.
    memory_api = _abrir_memoria(config, event_bus, logger)

    # 5. Interface web (Cap 13 s.4).
    conf_web = config.secao("display")["web"]
    webui = WebUIServer(event_bus, port=conf_web["port"], memory_api=memory_api, config=config)
    await webui.iniciar()

    registry.atualizar_estado(NOME_MODULO, EstadoModulo.RUNNING)
    await event_bus.publish(
        "system.ready",
        {"robot_name": secao_sistema["robot_name"], "modo": "motion_core"},
        prioridade=Prioridade.CRITICA,
    )
    await event_bus.aguardar_fila_vazia()
    logger.info(
        "Motion Core pronto - interface web em http://0.0.0.0:%d (Ctrl+C para sair)",
        conf_web["port"],
    )

    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sinal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sinal, parar.set)
    await parar.wait()

    logger.info("Motion Core: iniciando desligamento seguro")
    heartbeat.parar()
    tarefa_heartbeat.cancel()
    await webui.encerrar()
    await comm.encerrar()
    servidor_tcp.close()
    await servidor_tcp.wait_closed()
    event_bus.parar()
    tarefa_bus.cancel()
    for tarefa in (tarefa_heartbeat, tarefa_bus):
        try:
            await tarefa
        except asyncio.CancelledError:
            pass
    registry.atualizar_estado(NOME_MODULO, EstadoModulo.STOPPED)
    logger.info("Motion Core encerrado")


if __name__ == "__main__":
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        pass
