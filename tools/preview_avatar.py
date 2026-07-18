#!/usr/bin/env python3
"""Preview do avatar (Sentinelinha, Fase 8 / Cap 13) sem precisar do robo
inteiro ligado.

So roda no NOTEBOOK (Display e Notebook-only, EDR-0018) - precisa do extra
"display" instalado: pip install -e ".[display]"

Sobe o AvatarServer plugado num Event Bus real e fica publicando uma
sequencia de eventos de mentirinha (pan/tilt varrendo de um lado a outro,
estados de voz em ciclo) so para o avatar ter o que mostrar na tela sem
precisar da Vision/Voice Core rodando de verdade.

Uso:
    python tools/preview_avatar.py
    # abra http://127.0.0.1:8090 no navegador do Notebook
"""
from __future__ import annotations

import asyncio
import logging
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orion.display.avatar_server import AvatarServer  # noqa: E402
from orion.kernel.config import ConfigurationManager  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("preview_avatar")

CICLO_ESTADOS = ("IDLE", "LISTENING", "WAKE_DETECTED", "TRANSCRIBING", "THINKING", "SPEAKING")
INTERVALO_ESTADO_S = 3.0
INTERVALO_PAN_TILT_S = 0.1


async def _varrer_pan_tilt(bus: EventBus, limite_pan: float, limite_tilt: float) -> None:
    t = 0.0
    while True:
        pan = math.sin(t * 0.5) * limite_pan
        tilt = math.sin(t * 0.3) * limite_tilt * 0.5
        await bus.publish("motion.pan_tilt", {"pan": pan, "tilt": tilt})
        await asyncio.sleep(INTERVALO_PAN_TILT_S)
        t += INTERVALO_PAN_TILT_S


async def _ciclar_estados_de_voz(bus: EventBus) -> None:
    indice = 0
    while True:
        estado = CICLO_ESTADOS[indice % len(CICLO_ESTADOS)]
        logger.info("voice.status -> %s", estado)
        await bus.publish("voice.status", {"estado": estado})
        indice += 1
        await asyncio.sleep(INTERVALO_ESTADO_S)


async def principal() -> None:
    config = ConfigurationManager("config/orion.yaml").carregar()
    secao_visao = config.secao("vision")
    config_frontend = {
        "pan_limits_degrees": secao_visao["pan_limits_degrees"],
        "tilt_limits_degrees": secao_visao["tilt_limits_degrees"],
    }

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())

    servidor = AvatarServer(bus, config_frontend=config_frontend)
    await servidor.iniciar()
    await bus.publish("system.ready", {"robot_name": config.secao("system")["robot_name"]})

    logger.info("Preview do avatar em http://127.0.0.1:8090 - Ctrl+C para sair")

    limite_pan = max(abs(v) for v in config_frontend["pan_limits_degrees"])
    limite_tilt = max(abs(v) for v in config_frontend["tilt_limits_degrees"])

    try:
        await asyncio.gather(
            _varrer_pan_tilt(bus, limite_pan, limite_tilt),
            _ciclar_estados_de_voz(bus),
        )
    finally:
        await servidor.encerrar()
        bus.parar()
        tarefa_bus.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        pass
