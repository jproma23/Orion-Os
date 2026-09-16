"""Plugin desktop_control - Mouse, teclado, janelas (X11/Wayland via pyautogui)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import BasePlugin, PluginParameter, PluginResult, register_plugin, run_subprocess

logger = logging.getLogger("orion.mission.jarvis_plugins.desktop_control")


class DesktopControlPlugin(BasePlugin):
    name = "desktop_control"
    description = """Mouse, teclado, janelas (X11/Wayland via pyautogui)"""
    parameters_schema = [
        PluginParameter(
            name="action",
            type="string",
            description="Acao: mouse_move, mouse_click, mouse_scroll, keyboard_type, keyboard_hotkey, window_list, window_focus, window_move, window_resize, window_close",
            required=True,
            enum=["mouse_move", "mouse_click", "mouse_scroll", "keyboard_type", "keyboard_hotkey", "window_list", "window_focus", "window_move", "window_resize", "window_close"],
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
            return PluginResult.fail("Acao obrigatoria: mouse_move, mouse_click, mouse_scroll, keyboard_type, keyboard_hotkey, window_list, window_focus, window_move, window_resize, window_close")

        try:
            method = getattr(self, f"_action_{action}", None)
            if not method:
                return PluginResult.fail(f"Acao desconhecida: {action}")
            return await method(**params)
        except Exception as e:
            logger.exception("Erro em desktop_control")
            return PluginResult.fail(f"Erro: {type(e).__name__}: {e}")


    async def _action_mouse_move(self, x: int, y: int, duration: float = 0.5, **_) -> PluginResult:
        try:
            import pyautogui
            pyautogui.moveTo(x, y, duration=duration)
            return PluginResult.ok(f"Mouse movido para ({x}, {y})")
        except Exception as e:
            return PluginResult.fail(f"Erro no mouse_move: {e}")

    async def _action_mouse_click(self, x: int = None, y: int = None, button: str = "left", clicks: int = 1, **_) -> PluginResult:
        try:
            import pyautogui
            if x is not None and y is not None:
                pyautogui.click(x, y, button=button, clicks=clicks)
            else:
                pyautogui.click(button=button, clicks=clicks)
            return PluginResult.ok(f"Click {button} x{clicks}")
        except Exception as e:
            return PluginResult.fail(f"Erro no mouse_click: {e}")

    async def _action_mouse_scroll(self, amount: int, x: int = None, y: int = None, **_) -> PluginResult:
        try:
            import pyautogui
            if x is not None and y is not None:
                pyautogui.scroll(amount, x=x, y=y)
            else:
                pyautogui.scroll(amount)
            return PluginResult.ok(f"Scroll {amount}")
        except Exception as e:
            return PluginResult.fail(f"Erro no mouse_scroll: {e}")

    async def _action_keyboard_type(self, text: str, interval: float = 0.05, **_) -> PluginResult:
        try:
            import pyautogui
            pyautogui.write(text, interval=interval)
            return PluginResult.ok(f"Digitado: {text[:50]}...")
        except Exception as e:
            return PluginResult.fail(f"Erro no keyboard_type: {e}")

    async def _action_keyboard_hotkey(self, *keys: str, **_) -> PluginResult:
        try:
            import pyautogui
            pyautogui.hotkey(*keys)
            return PluginResult.ok(f"Hotkey: {'+'.join(keys)}")
        except Exception as e:
            return PluginResult.fail(f"Erro no keyboard_hotkey: {e}")

    async def _action_window_list(self, **_) -> PluginResult:
        try:
            import subprocess
            # X11: wmctrl -l
            code, stdout, stderr = await run_subprocess(["wmctrl", "-l"], timeout=5)
            if code == 0:
                windows = []
                for line in stdout.strip().split("\n"):
                    parts = line.split(None, 3)
                    if len(parts) >= 4:
                        windows.append({"id": parts[0], "desktop": parts[1], "pid": parts[2], "title": parts[3]})
                return PluginResult.ok(windows)
            return PluginResult.fail("wmctrl nao disponivel")
        except Exception as e:
            return PluginResult.fail(f"Erro no window_list: {e}")

    async def _action_window_focus(self, window_id: str = "", title: str = "", **_) -> PluginResult:
        try:
            import subprocess
            if window_id:
                cmd = ["wmctrl", "-i", "-a", window_id]
            elif title:
                cmd = ["wmctrl", "-a", title]
            else:
                return PluginResult.fail("Forneca window_id ou title")
            code, stdout, stderr = await run_subprocess(cmd, timeout=5)
            return PluginResult.ok("Janela focada" if code == 0 else stderr)
        except Exception as e:
            return PluginResult.fail(f"Erro no window_focus: {e}")

    async def _action_window_move(self, window_id: str, x: int, y: int, **_) -> PluginResult:
        try:
            import subprocess
            cmd = ["wmctrl", "-i", "-r", window_id, "-e", f"0,{x},{y},-1,-1"]
            code, stdout, stderr = await run_subprocess(cmd, timeout=5)
            return PluginResult.ok("Janela movida" if code == 0 else stderr)
        except Exception as e:
            return PluginResult.fail(f"Erro no window_move: {e}")

    async def _action_window_resize(self, window_id: str, width: int, height: int, **_) -> PluginResult:
        try:
            import subprocess
            cmd = ["wmctrl", "-i", "-r", window_id, "-e", f"0,-1,-1,{width},{height}"]
            code, stdout, stderr = await run_subprocess(cmd, timeout=5)
            return PluginResult.ok("Janela redimensionada" if code == 0 else stderr)
        except Exception as e:
            return PluginResult.fail(f"Erro no window_resize: {e}")

    async def _action_window_close(self, window_id: str, **_) -> PluginResult:
        try:
            import subprocess
            cmd = ["wmctrl", "-i", "-c", window_id]
            code, stdout, stderr = await run_subprocess(cmd, timeout=5)
            return PluginResult.ok("Janela fechada" if code == 0 else stderr)
        except Exception as e:
            return PluginResult.fail(f"Erro no window_close: {e}")


register_plugin(DesktopControlPlugin())
