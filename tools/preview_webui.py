#!/usr/bin/env python3
"""Preview da interface web (Fase 8 / Cap 13 s.4-5) sem precisar do robo
inteiro ligado - mesmo espirito do tools/preview_avatar.py, mas pro lado
do Raspberry.

Sobe o WebUIServer plugado num Event Bus real e fica publicando uma
sequencia de eventos de mentirinha (telemetria, posicao, missao, estados
de seguranca) pra ter o que mostrar no dashboard sem precisar da
Navigation/FusaoSensores reais rodando com hardware fisico.

Uso:
    python tools/preview_webui.py
    # abra http://<ip-do-raspberry>:8080 de qualquer dispositivo da rede
"""
from __future__ import annotations

import asyncio
import logging
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motion_core.memory.api import MemoryAPI  # noqa: E402
from motion_core.memory.database import DatabaseManager  # noqa: E402
from motion_core.webui.server import WebUIServer  # noqa: E402
from orion.kernel.config import ConfigurationManager  # noqa: E402
from orion.kernel.event_bus import EventBus, Prioridade  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("preview_webui")

MODOS_NAVEGACAO = ("HOLD", "GOTO", "PATROL", "HOLD")
INTERVALO_TELEMETRIA_S = 1.0
INTERVALO_MISSAO_S = 4.0


async def _publicar_telemetria_e_posicao(bus: EventBus) -> None:
    t = 0.0
    while True:
        distancia = 40 + 30 * math.sin(t * 0.3)
        await bus.publish(
            "comm.mensagem.telemetry",
            {
                "payload": {
                    "estado": "EXECUTING_MISSION" if int(t) % 6 < 3 else "IDLE",
                    "em_movimento": int(t) % 6 < 3,
                    "distancia_frontal_cm": round(distancia, 1),
                    "temperatura_c": 24.5,
                    "umidade_percent": 55.0,
                    "inclinacao_graus": round(2 * math.sin(t * 0.1), 1),
                }
            },
        )
        await bus.publish(
            "motion.position",
            {
                "x_m": round(math.cos(t * 0.1), 3),
                "y_m": round(math.sin(t * 0.1), 3),
                "orientacao_graus": round((t * 5) % 360, 1),
                "velocidade_m_s": round(abs(math.sin(t * 0.2)) * 0.3, 3),
            },
        )
        await asyncio.sleep(INTERVALO_TELEMETRIA_S)
        t += INTERVALO_TELEMETRIA_S


ANGULOS_RADAR = (0, 30, 60, 90, 120, 150, 180)


async def _publicar_varreduras(bus: EventBus) -> None:
    t = 0.0
    while True:
        leituras = []
        for angulo in ANGULOS_RADAR:
            distancia = 60 + 80 * abs(math.sin(math.radians(angulo) + t * 0.4))
            valida = angulo != 90 or int(t) % 10 < 8  # de vez em quando some a leitura central
            leituras.append(
                {
                    "angulo": angulo,
                    "distancia_cm": round(distancia, 1) if valida else -1,
                    "valida": valida,
                }
            )
        await bus.publish("motion.scan_complete", {"leituras": leituras})
        await asyncio.sleep(3.0)
        t += 3.0


async def _ciclar_missao(bus: EventBus) -> None:
    indice = 0
    while True:
        modo = MODOS_NAVEGACAO[indice % len(MODOS_NAVEGACAO)]
        await bus.publish("navigation.mode_changed", {"modo": modo})
        await bus.publish(
            "navigation.segment_started", {"graus": 90, "distancia_cm": 50}
        )
        await asyncio.sleep(INTERVALO_MISSAO_S / 2)
        await bus.publish(
            "navigation.segment_completed", {"graus": 90, "distancia_cm": 50}
        )
        indice += 1
        await asyncio.sleep(INTERVALO_MISSAO_S / 2)


async def _piscar_safe_mode(bus: EventBus) -> None:
    while True:
        await asyncio.sleep(15.0)
        await bus.publish(
            "safety.safe_mode_entered",
            {"motivo": "impacto_detectado (simulado)"},
            prioridade=Prioridade.CRITICA,
        )
        await asyncio.sleep(4.0)
        await bus.publish("safety.safe_mode_exited", {}, prioridade=Prioridade.ALTA)


async def _preparar_memoria_de_demonstracao(bus: EventBus) -> MemoryAPI:
    # banco temporario, so pra esta preview ter algo pra mostrar na pagina
    # CONVERSA - nunca toca no caminho real de producao (config/orion.yaml
    # aponta pro SSD, que nao esta montado nesta maquina de dev).
    diretorio = Path(tempfile.mkdtemp(prefix="orion_preview_webui_"))
    db = DatabaseManager(diretorio / "orion.db", diretorio / "backups")
    db.iniciar()
    memory = MemoryAPI(db, bus)
    await memory.remember("conversas", {"pessoa_id": None, "papel": "usuario", "texto": "Fofão, que horas são?"})
    await memory.remember("conversas", {"pessoa_id": None, "papel": "robo", "texto": "Agora são 21:45."})
    await memory.remember("conversas", {"pessoa_id": None, "papel": "usuario", "texto": "Fofão, acenda a lanterna"})
    await memory.remember("conversas", {"pessoa_id": None, "papel": "robo", "texto": "Lanterna ligada."})
    return memory


async def principal() -> None:
    config = ConfigurationManager("config/orion.yaml").carregar()

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())

    memory = await _preparar_memoria_de_demonstracao(bus)
    servidor = WebUIServer(
        bus, port=config.secao("display")["web"]["port"], memory_api=memory, config=config
    )
    await servidor.iniciar()
    await bus.publish(
        "system.ready", {"robot_name": config.secao("system")["robot_name"], "modo": "PREVIEW"}
    )

    logger.info(
        "Preview da interface web em http://0.0.0.0:%d - Ctrl+C para sair",
        config.secao("display")["web"]["port"],
    )

    try:
        await asyncio.gather(
            _publicar_telemetria_e_posicao(bus),
            _publicar_varreduras(bus),
            _ciclar_missao(bus),
            _piscar_safe_mode(bus),
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
