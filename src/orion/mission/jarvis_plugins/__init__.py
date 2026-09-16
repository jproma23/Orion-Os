"""JARVIS Plugins para ORION - Function Calling Capabilities.

Plugins disponiveis:
- code_executor: Python REPL persistente, execucao de arquivos/scripts
- file_operations: read/write/glob/grep/edit/list arquivos
- system_control: processos, systemd, journalctl, apps
- screen_awareness: screenshot + tesseract OCR + analise Gemini Vision
- desktop_control: mouse/teclado/janela (X11/Wayland via pyautogui)
- ros2_navigator: Nav2 goals, pose, mapa, cancel via bridge HTTP

Uso:
    from orion.mission.jarvis_plugins import get_registry, execute_plugin

    registry = get_registry()
    schemas = registry.get_function_schemas()  # para Gemini tools

    result = await execute_plugin("code_executor", code="print(1+1)")
    result = await execute_plugin("file_operations", operation="read", path="~/arquivo.txt")
"""
from .base import get_registry, execute_plugin  # noqa: F401
