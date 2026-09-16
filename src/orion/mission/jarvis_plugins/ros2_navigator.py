"""Plugin ros2_navigator - Integracao Nav2 via bridge HTTP (goal, cancel, pose, map, status, follow)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import BasePlugin, PluginParameter, PluginResult, register_plugin, run_subprocess

logger = logging.getLogger("orion.mission.jarvis_plugins.ros2_navigator")


class Ros2NavigatorPlugin(BasePlugin):
    name = "ros2_navigator"
    description = """Integracao Nav2 via bridge HTTP (goal, cancel, pose, map, status, follow)"""
    parameters_schema = [
        PluginParameter(
            name="action",
            type="string",
            description="Acao: goto, cancel, pose, map, status, follow",
            required=True,
            enum=["goto", "cancel", "pose", "map", "status", "follow"],
        ),
        PluginParameter(
            name="params",
            type="object",
            description="Parametros da acao",
            required=False,
            default={},
        ),
    ]

    async def execute(self, **kwargs) -> PluginResult:
        action = kwargs.get("action")
        params = kwargs.get("params", {})
        
        if not action:
            return PluginResult.fail("Acao obrigatoria: goto, cancel, pose, map, status, follow")

        try:
            method = getattr(self, f"_action_{action}", None)
            if not method:
                return PluginResult.fail(f"Acao desconhecida: {action}")
            return await method(**params)
        except Exception as e:
            logger.exception("Erro em ros2_navigator")
            return PluginResult.fail(f"Erro: {type(e).__name__}: {e}")


    BRIDGE_GOAL_URL = "http://127.0.0.1:8081/goal"
    BRIDGE_CANCEL_URL = "http://127.0.0.1:8081/cancelar"
    ESTADO_URL = "http://127.0.0.1:8080/estado"

    async def _action_goto(self, x: float, y: float, yaw: float = 0.0, **_) -> PluginResult:
        try:
            import urllib.request, json
            payload = json.dumps({"x": x, "y": y, "yaw_rad": yaw}).encode()
            req = urllib.request.Request(self.BRIDGE_GOAL_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
            return PluginResult.ok(result, metadata={"x": x, "y": y, "yaw": yaw})
        except Exception as e:
            return PluginResult.fail(f"Erro no goto: {e}")

    async def _action_cancel(self, **_) -> PluginResult:
        try:
            import urllib.request
            req = urllib.request.Request(self.BRIDGE_CANCEL_URL, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return PluginResult.ok("Goal cancelado")
        except Exception as e:
            return PluginResult.fail(f"Erro no cancel: {e}")

    async def _action_pose(self, **_) -> PluginResult:
        try:
            import urllib.request, json
            with urllib.request.urlopen(self.ESTADO_URL, timeout=5) as resp:
                estado = json.loads(resp.read()).get("estado", {})
            pos = estado.get("posicao", {})
            return PluginResult.ok({
                "x": pos.get("x_m", 0),
                "y": pos.get("y_m", 0),
                "yaw": pos.get("orientacao_graus", 0),
            })
        except Exception as e:
            return PluginResult.fail(f"Erro obtendo pose: {e}")

    async def _action_map(self, **_) -> PluginResult:
        try:
            import urllib.request, json
            with urllib.request.urlopen(self.ESTADO_URL, timeout=5) as resp:
                estado = json.loads(resp.read()).get("estado", {})
            mapa = estado.get("mapa", {})
            return PluginResult.ok(mapa, metadata={"has_map": bool(mapa)})
        except Exception as e:
            return PluginResult.fail(f"Erro obtendo mapa: {e}")

    async def _action_status(self, **_) -> PluginResult:
        try:
            import urllib.request, json
            with urllib.request.urlopen(self.ESTADO_URL, timeout=5) as resp:
                estado = json.loads(resp.read()).get("estado", {})
            nav = estado.get("navegacao", {})
            return PluginResult.ok(nav)
        except Exception as e:
            return PluginResult.fail(f"Erro no status: {e}")

    async def _action_follow(self, person: str = "", enable: bool = True, **_) -> PluginResult:
        return PluginResult.fail("Follow precisa integracao com Vision Core - TODO")


register_plugin(Ros2NavigatorPlugin())
