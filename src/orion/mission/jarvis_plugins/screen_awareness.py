"""Plugin screen_awareness - Screenshot, OCR (tesseract), analise Gemini Vision."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import BasePlugin, PluginParameter, PluginResult, register_plugin, run_subprocess

logger = logging.getLogger("orion.mission.jarvis_plugins.screen_awareness")


class ScreenAwarenessPlugin(BasePlugin):
    name = "screen_awareness"
    description = """Screenshot, OCR (tesseract), analise Gemini Vision"""
    parameters_schema = [
        PluginParameter(
            name="action",
            type="string",
            description="Acao: screenshot, ocr, analyze, watch",
            required=True,
            enum=["screenshot", "ocr", "analyze", "watch"],
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
            return PluginResult.fail("Acao obrigatoria: screenshot, ocr, analyze, watch")

        try:
            method = getattr(self, f"_action_{action}", None)
            if not method:
                return PluginResult.fail(f"Acao desconhecida: {action}")
            return await method(**params)
        except Exception as e:
            logger.exception("Erro em screen_awareness")
            return PluginResult.fail(f"Erro: {type(e).__name__}: {e}")


    async def _action_screenshot(self, region: dict = None, display: str = ":0", **_) -> PluginResult:
        try:
            import subprocess, tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                out_path = tmp.name
            cmd = ["import", "-window", "root", out_path] if not region else ["import", "-crop", f"{region.get('width')}x{region.get('height')}+{region.get('x')}+{region.get('y')}", out_path]
            code, stdout, stderr = await run_subprocess(cmd, timeout=10)
            if code == 0:
                return PluginResult.ok(f"Screenshot salvo: {out_path}", metadata={"path": out_path})
            return PluginResult.fail(f"Erro no import: {stderr}")
        except Exception as e:
            return PluginResult.fail(f"Erro no screenshot: {e}")

    async def _action_ocr(self, image_path: str = "", **_) -> PluginResult:
        try:
            if not image_path:
                return PluginResult.fail("image_path obrigatorio")
            import subprocess
            cmd = ["tesseract", image_path, "stdout", "-l", "por+eng"]
            code, stdout, stderr = await run_subprocess(cmd, timeout=30)
            if code == 0:
                return PluginResult.ok(stdout.strip() or "[sem texto detectado]", metadata={"image": image_path})
            return PluginResult.fail(f"Erro no tesseract: {stderr}")
        except Exception as e:
            return PluginResult.fail(f"Erro no OCR: {e}")

    async def _action_analyze(self, image_path: str = "", prompt: str = "O que tem nesta imagem?", **_) -> PluginResult:
        try:
            if not image_path:
                return PluginResult.fail("image_path obrigatorio")
            # Integração com Gemini Vision via AiManager (precisa ser injetado)
            return PluginResult.fail("Analise Gemini Vision precisa de integracao com AiManager - TODO")
        except Exception as e:
            return PluginResult.fail(f"Erro na analise: {e}")

    async def _action_watch(self, interval: float = 1.0, threshold: float = 0.1, **_) -> PluginResult:
        return PluginResult.fail("Watch nao implementado - TODO")


register_plugin(ScreenAwarenessPlugin())
