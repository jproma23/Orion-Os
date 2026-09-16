"""Plugin plugin_registry - Registry e utilitarios de integracao JARVIS no MissionPlanner."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import BasePlugin, PluginParameter, PluginResult, register_plugin, run_subprocess

logger = logging.getLogger("orion.mission.jarvis_plugins.plugin_registry")


class PluginRegistryPlugin(BasePlugin):
    name = "plugin_registry"
    description = """Registry e utilitarios de integracao JARVIS no MissionPlanner"""
    parameters_schema = [
        PluginParameter(
            name="action",
            type="string",
            description="Acao: list_schemas, execute, get_config",
            required=True,
            enum=["list_schemas", "execute", "get_config"],
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
            return PluginResult.fail("Acao obrigatoria: list_schemas, execute, get_config")

        try:
            method = getattr(self, f"_action_{action}", None)
            if not method:
                return PluginResult.fail(f"Acao desconhecida: {action}")
            return await method(**params)
        except Exception as e:
            logger.exception("Erro em plugin_registry")
            return PluginResult.fail(f"Erro: {type(e).__name__}: {e}")


    async def _action_list_schemas(self, **_) -> PluginResult:
        from .base import get_registry
        registry = get_registry()
        return PluginResult.ok(registry.get_function_schemas())

    async def _action_execute(self, plugin: str = "", action: str = "", params: dict = None, **_) -> PluginResult:
        from .base import execute_plugin
        if not plugin or not action:
            return PluginResult.fail("plugin e action obrigatorios")
        # Executar plugin -> action via params
        kwargs = params or {}
        kwargs["action"] = action
        return await execute_plugin(plugin, **kwargs)

    async def _action_get_config(self, **_) -> PluginResult:
        return PluginResult.ok({
            "plugins": ["code_executor", "file_operations", "system_control", "screen_awareness", "ros2_navigator", "desktop_control"],
            "gemini_function_calling": True,
            "bridge_goal_url": "http://127.0.0.1:8081/goal",
            "bridge_cancel_url": "http://127.0.0.1:8081/cancelar",
            "estado_url": "http://127.0.0.1:8080/estado",
        })


register_plugin(PluginRegistryPlugin())
