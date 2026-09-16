"""Forwarder de GOAL: entrega o destino do Motion Core ao Nav2.

O Nav2 (NaviCore) e quem resolve COMO chegar - mapa, costmap, planejamento,
replanejamento e desvio. O Motion Core (host) publica `navigation.goal`
(com x_m, y_m em metros no frame do mapa) quando o BehaviorCore/IA decide
um destino; este modulo traduz para o bridge_goal do container
(`nav2/src/orion_nav2/orion_nav2/bridge_goal.py`), que envia a acao
NavigateToPose e devolve o resultado.

Nao decide destino: e ponte. Quem decide e a camada de comportamento
(BehaviorCore/IA estrategica). O bridge_goal escuta em 127.0.0.1:8081
(container com network_mode: host, acesso local sem senha).

O resultado volta para o bus como `navigation.goal_resultado` (completado,
falhou, cancelado, timeout) para quem pediu saber se o robo chegou.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import urllib.request

from orion.kernel.event_bus import EventBus, Evento, Prioridade

logger = logging.getLogger("motion_core.navigation.nav2_goal")

#: Endereco do bridge_goal (no container, network_mode host = 127.0.0.1 local).
URL_GOAL = "http://127.0.0.1:8081/goal"
URL_CANCELAR = "http://127.0.0.1:8081/cancelar"
#: Quanto tempo esperar o Nav2 completar o goal antes de desistir.
TIMEOUT_GOAL_S = 600.0
#: Intervalo minimo entre o pedido de um goal e a proxima aceitacao
#: (evita reenvio em laco se o resultado for "falhou" persistente).
COOLDOWN_FALHA_S = 5.0


class Nav2Goal:
    """Assina `navigation.goal`, repassa ao Nav2 e publica o resultado."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._proxima_tentativa_s = 0.0
        event_bus.subscribe("navigation.goal", self._ao_goal)

    async def _ao_goal(self, evento: Evento) -> None:
        """Handler nao bloqueante: o despacho do bus faz `await` no handler,
        entao qualquer espera aqui travaria todos os outros eventos. O goal
        roda numa task propria e o handler volta na hora."""
        dados = evento.dados
        try:
            x = float(dados["x_m"])
            y = float(dados["y_m"])
            yaw = float(dados.get("yaw_rad", 0.0))
        except (KeyError, TypeError, ValueError):
            logger.warning("navigation.goal sem x_m/y_m validos: %r", dados)
            return
        asyncio.create_task(self._processar_goal(x, y, yaw))

    async def _processar_goal(self, x: float, y: float, yaw: float) -> None:
        agora = asyncio.get_running_loop().time()
        if agora < self._proxima_tentativa_s:
            logger.warning("goal %r ignorado (cooldown apos falha)", (x, y))
            return

        payload = {"x": x, "y": y, "yaw_rad": yaw}
        try:
            resultado, status = await asyncio.to_thread(self._enviar, payload)
        except Exception as exc:
            logger.error("falha ao falar com o bridge_goal: %s", exc)
            await self._publicar("falhou", {"motivo": str(exc)})
            return

        if resultado != "completado":
            self._proxima_tentativa_s = (
                asyncio.get_running_loop().time() + COOLDOWN_FALHA_S
            )
        await self._publicar(resultado, {"x_m": x, "y_m": y, "yaw_rad": yaw, "status": status})

    def _enviar(self, payload: dict[str, Any]) -> tuple[str, int]:
        """POST sincrono ao bridge_goal; roda fora do loop (to_thread)."""
        req = urllib.request.Request(
            URL_GOAL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_GOAL_S) as resp:
            corpo = json.loads(resp.read())
        return corpo.get("resultado", "falhou"), resp.status

    async def _publicar(self, resultado: str, detalhes: dict[str, Any]) -> None:
        await self._event_bus.publish(
            "navigation.goal_resultado",
            {"resultado": resultado, **detalhes},
            prioridade=Prioridade.ALTA,
        )
        logger.info("Nav2 goal %s: %s", resultado, detalhes)