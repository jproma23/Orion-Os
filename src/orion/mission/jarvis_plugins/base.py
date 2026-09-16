"""Base classes para plugins JARVIS no ORION.

Define a interface comum, decorators para function calling,
e utilitários de execução segura.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("orion.mission.jarvis_plugins")


@dataclass
class PluginResult:
    """Resultado padronizado de execução de plugin."""
    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def ok(cls, data: Any = None, **metadata) -> "PluginResult":
        return cls(success=True, data=data, metadata=metadata)

    @classmethod
    def fail(cls, error: str, **metadata) -> "PluginResult":
        return cls(success=False, error=error, metadata=metadata)


@dataclass
class PluginParameter:
    """Descrição de parâmetro para function calling schema."""
    name: str
    type: str  # "string", "number", "boolean", "array", "object"
    description: str
    required: bool = True
    enum: list | None = None
    default: Any = None


class BasePlugin(ABC):
    """Classe base para todos os plugins JARVIS.

    Cada plugin deve implementar:
    - name: identificador único (ex: "code_executor")
    - description: descrição humana do que faz
    - parameters_schema: lista de PluginParameter para function calling
    - execute(): método principal assíncrono
    """

    name: str = ""
    description: str = ""
    parameters_schema: list[PluginParameter] = []

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._validate_config()

    def _validate_config(self) -> None:
        """Validação opcional de configuração no init."""
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> PluginResult:
        """Executa a ação do plugin.

        Args:
            **kwargs: parâmetros validados contra parameters_schema

        Returns:
            PluginResult com success, data, error, metadata
        """
        pass

    def to_function_schema(self) -> dict:
        """Converte para schema OpenAI function calling."""
        properties = {}
        required = []
        for param in self.parameters_schema:
            prop = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum:
                prop["enum"] = param.enum
            if param.default is not None:
                prop["default"] = param.default
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


class PluginRegistry:
    """Registry central de plugins JARVIS.

    Gerencia registro, descoberta, validação de parâmetros,
    e geração de schemas para function calling.
    """

    def __init__(self):
        self._plugins: dict[str, BasePlugin] = {}
        self._schemas_cache: dict[str, dict] | None = None

    def register(self, plugin: BasePlugin) -> None:
        """Registra uma instância de plugin."""
        if not plugin.name:
            raise ValueError("Plugin deve ter 'name' não vazio")
        if plugin.name in self._plugins:
            logger.warning("Plugin '%s' já registrado, sobrescrevendo", plugin.name)
        self._plugins[plugin.name] = plugin
        self._schemas_cache = None
        logger.info("Plugin registrado: %s", plugin.name)

    def unregister(self, name: str) -> bool:
        """Remove um plugin do registry."""
        if name in self._plugins:
            del self._plugins[name]
            self._schemas_cache = None
            return True
        return False

    def get(self, name: str) -> BasePlugin | None:
        """Obtém plugin por nome."""
        return self._plugins.get(name)

    def list_plugins(self) -> list[dict]:
        """Lista plugins registrados com metadados básicos."""
        return [
            {
                "name": p.name,
                "description": p.description,
                "parameters": len(p.parameters_schema),
            }
            for p in self._plugins.values()
        ]

    def get_function_schemas(self) -> list[dict]:
        """Retorna schemas OpenAI function calling para todos os plugins."""
        if self._schemas_cache is None:
            self._schemas_cache = {
                name: plugin.to_function_schema()
                for name, plugin in self._plugins.items()
            }
        return list(self._schemas_cache.values())

    async def execute(self, name: str, **kwargs) -> PluginResult:
        """Executa um plugin por nome com validação de parâmetros."""
        plugin = self._plugins.get(name)
        if not plugin:
            return PluginResult.fail(f"Plugin não encontrado: {name}")

        # Validar parâmetros obrigatórios
        missing = [
            p.name for p in plugin.parameters_schema
            if p.required and p.name not in kwargs and p.default is None
        ]
        if missing:
            return PluginResult.fail(
                f"Parâmetros obrigatórios faltando: {', '.join(missing)}"
            )

        # Filtrar apenas parâmetros conhecidos
        known_params = {p.name for p in plugin.parameters_schema}
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in known_params}

        try:
            logger.info("Executando plugin '%s' com args: %s", name, filtered_kwargs)
            start = time.monotonic()
            result = await plugin.execute(**filtered_kwargs)
            duration = time.monotonic() - start
            logger.info("Plugin '%s' concluído em %.3fs (success=%s)",
                       name, duration, result.success)
            return result
        except Exception as e:
            logger.exception("Erro ao executar plugin '%s'", name)
            return PluginResult.fail(f"Erro interno: {type(e).__name__}: {e}")

    def get_tools_for_gemini(self) -> list[dict]:
        """Retorna formato compatível com Gemini function calling."""
        return [
            {"function_declarations": self.get_function_schemas()}
        ]


# Instância global do registry
_registry = PluginRegistry()


def get_registry() -> PluginRegistry:
    """Obtém o registry global (singleton)."""
    return _registry


def register_plugin(plugin: BasePlugin) -> None:
    """Registra plugin no registry global."""
    _registry.register(plugin)


async def execute_plugin(name: str, **kwargs) -> PluginResult:
    """Executa plugin via registry global."""
    return await _registry.execute(name, **kwargs)


# Utilitários comuns para plugins

async def run_subprocess(
    cmd: list[str] | str,
    cwd: Path | str | None = None,
    timeout: float = 30.0,
    capture_output: bool = True,
    input_data: str | None = None,
) -> tuple[int, str, str]:
    """Executa subprocesso de forma segura com timeout.

    Args:
        cmd: Comando como lista ou string (shell=True se string)
        cwd: Diretório de trabalho
        timeout: Timeout em segundos
        capture_output: Se True, captura stdout/stderr
        input_data: Dados para stdin

    Returns:
        (returncode, stdout, stderr)
    """
    if isinstance(cmd, str):
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE if capture_output else None,
            stderr=asyncio.subprocess.PIPE if capture_output else None,
            stdin=asyncio.subprocess.PIPE if input_data else None,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE if capture_output else None,
            stderr=asyncio.subprocess.PIPE if capture_output else None,
            stdin=asyncio.subprocess.PIPE if input_data else None,
        )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input_data.encode() if input_data else None),
            timeout=timeout,
        )
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        return proc.returncode or 0, stdout, stderr
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"Comando excedeu timeout de {timeout}s: {cmd}")


def sanitize_path(path: str, base: Path | None = None) -> Path:
    """Sanitiza caminho para evitar path traversal.

    Args:
        path: Caminho relativo ou absoluto
        base: Diretório base permitido (default: cwd)

    Returns:
        Path resolvido e validado

    Raises:
        ValueError: Se caminho tentar escapar do base
    """
    base = Path(base or Path.cwd()).resolve()
    target = Path(path).expanduser().resolve()

    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError(f"Caminho fora do diretório permitido: {path}")

    return target


def truncate_output(text: str, max_chars: int = 8000) -> str:
    """Trunca output longo preservando início e fim."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n\n... [{len(text) - max_chars} chars truncados] ...\n\n" + text[-half:]


# Decorator para métodos de plugin (opcional, para validação automática)
def plugin_method(func: Callable) -> Callable:
    """Decorator que valida args contra schema do plugin pai."""
    sig = inspect.signature(func)
    return func