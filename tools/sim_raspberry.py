#!/usr/bin/env python3
"""Simulador do Raspberry Pi / Motion Core (Fase 2, Cap 14).

Sobe um servidor TCP que fala o protocolo oficial (Cap 5, 14): aceita a
conexao do Notebook, responde WHO_ARE_YOU e ACKa COMMAND automaticamente
(via ComunicacaoService) e envia heartbeat periodico - o suficiente para
testar o Mission Core (Notebook) sem um Raspberry real.

Uso:
    python tools/sim_raspberry.py [--host 0.0.0.0] [--port 5757]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orion.communication.heartbeat import MonitorHeartbeat  # noqa: E402
from orion.communication.service import ComunicacaoService  # noqa: E402
from orion.communication.transport import ConexaoTcp, iniciar_servidor_tcp  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sim_raspberry")

NOME_MODULO = "motion_core"
VERSAO_SIMULADOR = "sim-0.1.0"

_TOPICOS_DIAGNOSTICO = (
    "comm.module_lost",
    "comm.module_recovered",
    "comm.link_degraded",
    "comm.protocol_mismatch",
)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5757)
    parser.add_argument("--intervalo-heartbeat", type=float, default=1.0)
    args = parser.parse_args()

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())

    for topico in _TOPICOS_DIAGNOSTICO:
        bus.subscribe(topico, lambda e, t=topico: logger.info("evento %s: %s", t, e.dados))

    servico = ComunicacaoService(NOME_MODULO, bus, versao_modulo=VERSAO_SIMULADOR)
    monitor = MonitorHeartbeat(servico, bus, intervalo_s=args.intervalo_heartbeat)

    async def ao_conectar(conexao: ConexaoTcp) -> None:
        logger.info("Notebook conectado")
        servico.adicionar_link("mission_core", conexao)
        monitor.monitorar("mission_core")

    servidor = await iniciar_servidor_tcp(args.host, args.port, ao_conectar)
    logger.info("Raspberry simulado escutando em %s:%d", args.host, args.port)

    tarefa_monitor = asyncio.create_task(monitor.iniciar())
    try:
        async with servidor:
            await servidor.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        monitor.parar()
        tarefa_monitor.cancel()
        await servico.encerrar()
        bus.parar()
        await tarefa_bus


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nEncerrando simulador...")
