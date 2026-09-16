"""Plugin Code Executor - Python REPL persistente e execução de arquivos.

Capacidades:
- Executa código Python em REPL persistente (mantém estado entre chamadas)
- Executa arquivos .py com captura de output
- Instala pacotes via pip
- Gerencia variáveis de sessão
- Timeout configurável
- Sandbox básico (sem acesso a __import__ perigosos por padrão)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path
from typing import Any

from .base import BasePlugin, PluginParameter, PluginResult, register_plugin, sanitize_path, truncate_output, run_subprocess

logger = logging.getLogger("orion.mission.jarvis_plugins.code_executor")


class PythonREPL:
    """REPL Python persistente com namespace isolado."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self._namespace: dict[str, Any] = {
            "__name__": "__jarvis_repl__",
            "__doc__": None,
        }
        self._init_builtins()
        self._execution_count = 0

    def _init_builtins(self) -> None:
        """Inicializa builtins seguros no namespace."""
        import builtins
        # Copiar builtins seguros
        safe_builtins = {
            k: v for k, v in builtins.__dict__.items()
            if k not in {"__import__", "eval", "exec", "compile", "open",
                         "input", "exit", "quit", "help", "license", "copyright",
                         "breakpoint", "vars", "globals", "locals"}
        }
        self._namespace["__builtins__"] = safe_builtins
        # Adicionar print customizado que captura output
        self._namespace["print"] = self._capturing_print

    def _capturing_print(self, *args, **kwargs) -> None:
        """Print que captura para o buffer de output."""
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        file = kwargs.get("file", sys.stdout)
        output = sep.join(str(a) for a in args) + end
        if hasattr(file, "write"):
            file.write(output)
        self._last_output = getattr(self, "_last_output", "") + output

    def execute(self, code: str) -> tuple[str, str, bool]:
        """Executa código no namespace do REPL.

        Returns:
            (stdout, stderr, success)
        """
        self._last_output = ""
        old_stdout = sys.stdout
        old_stderr = sys.stderr

        class OutputCapture:
            def __init__(self, repl):
                self.repl = repl
                self.buffer = ""

            def write(self, s):
                self.buffer += s
                self.repl._last_output = self.buffer

            def flush(self):
                pass

        stdout_capture = OutputCapture(self)
        stderr_capture = OutputCapture(self)

        sys.stdout = stdout_capture
        sys.stderr = stderr_capture

        try:
            # Compilar e executar
            compiled = compile(code, f"<jarvis_repl_{self._execution_count}>", "exec")
            exec(compiled, self._namespace)
            success = True
            stderr = ""
        except Exception as e:
            success = False
            stderr = f"{type(e).__name__}: {e}"
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        stdout = self._last_output
        self._execution_count += 1
        return stdout, stderr, success


class CodeExecutorPlugin(BasePlugin):
    name = "code_executor"
    description = (
        "Executa código Python em REPL persistente (mantém estado entre chamadas) "
        "ou executa arquivos .py. Suporta instalação de pacotes via pip. "
        "Ùtil para debugging, cálculos, automação, testes rápidos."
    )
    parameters_schema = [
        PluginParameter(
            name="code",
            type="string",
            description="Código Python para executar no REPL persistente",
            required=False,
        ),
        PluginParameter(
            name="file_path",
            type="string",
            description="Caminho para arquivo .py para executar (alternativa a code)",
            required=False,
        ),
        PluginParameter(
            name="install_package",
            type="string",
            description="Nome do pacote para instalar via pip (ex: 'requests')",
            required=False,
        ),
        PluginParameter(
            name="reset_session",
            type="boolean",
            description="Se true, reinicia o REPL limpando todo estado",
            required=False,
            default=False,
        ),
        PluginParameter(
            name="timeout",
            type="number",
            description="Timeout em segundos (padrão: 30)",
            required=False,
            default=30,
        ),
    ]

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self._repl = PythonREPL(timeout=config.get("default_timeout", 30.0) if config else 30.0)
        self._allowed_imports = config.get("allowed_imports", []) if config else []

    async def execute(self, **kwargs) -> PluginResult:
        code = kwargs.get("code")
        file_path = kwargs.get("file_path")
        install_package = kwargs.get("install_package")
        reset_session = kwargs.get("reset_session", False)
        timeout = kwargs.get("timeout", 30)

        if reset_session:
            self._repl = PythonREPL(timeout=timeout)
            return PluginResult.ok("Sessão REPL reiniciada", metadata={"action": "reset"})

        if install_package:
            return await self._install_package(install_package, timeout)

        if file_path:
            return await self._execute_file(file_path, timeout)

        if code:
            return await self._execute_code(code, timeout)

        return PluginResult.fail("Forneça 'code', 'file_path', 'install_package' ou 'reset_session=true'")

    async def _execute_code(self, code: str, timeout: float) -> PluginResult:
        """Executa código no REPL persistente."""
        try:
            # Dedent para aceitar código indentado
            code = textwrap.dedent(code)

            # Executar em thread para não bloquear event loop
            loop = asyncio.get_event_loop()
            stdout, stderr, success = await asyncio.wait_for(
                loop.run_in_executor(None, self._repl.execute, code),
                timeout=timeout,
            )

            output = truncate_output(stdout)
            if stderr:
                output += f"\n[stderr] {truncate_output(stderr)}"

            if success:
                return PluginResult.ok(
                    output or "[sem output]",
                    metadata={"execution_count": self._repl._execution_count}
                )
            else:
                return PluginResult.fail(output or stderr, metadata={"execution_count": self._repl._execution_count})

        except asyncio.TimeoutError:
            return PluginResult.fail(f"Timeout ({timeout}s) executando código")
        except Exception as e:
            return PluginResult.fail(f"Erro: {type(e).__name__}: {e}")
    async def _execute_file(self, file_path: str, timeout: float) -> PluginResult:
        """Executa arquivo .py."""
        try:
            path = sanitize_path(file_path, Path.home())
            if not path.exists():
                return PluginResult.fail("Arquivo nêo encontrado: {file_path}")
            if path.suffix != ".py":
                return PluginResult.fail("Apenas arquivos .py cào permitidos")
            code = path.read_text(encoding="utf-8")
            return await self._execute_code(code, timeout)
        except ValueError as e:
            return PluginResult.fail(str(e))
        except Exception as e:
            return PluginResult.fail(f"Erro lendo arquivop: {e}")
    async def _install_package(self, package: str, timeout: float) -> PluginResult:
        """Instala pacote via pip."""
        try:
            # Validar nome do pacote (básico)
            if not package.replace("-", "").replace("_", "").replace(".", "").isalnum():
                return PluginResult.fail("Nome de pacote inv,álido")

            cmd = [sys.executable, "-m", "pip", "install", package]
            returncode, stdout, stderr = await run_subprocess(cmd, timeout=timeout * 2)
            output = truncate_output(stdout + "\n" + stderr)
            if returncode == 0:
                return PluginResult.ok(f"Pacote '{package}' instalado com sucesso\n{stdout}")
            else:
                return PluginResult.fail(f"Falha ao instalar '{package}': {output}")
        except asyncio.TimeoutError:
            return PluginResult.fail(f"Timeout instalando pacote")
        except Exception as e:
            return PluginResult.fail(f"Erro instalando pacote: {e}")

# Instância global do REPL (singleton por processo)
_repl_instance: CodeExecutorPlugin | None = None


def get_code_executor(config: dict | None = None) -> CodeExecutorPlugin:
    """Obtém instância singleton do CodeExecutor."""
    global _repl_instance
    if _repl_instance is None:
        _repl_instance = CodeExecutorPlugin(config)
        register_plugin(_repl_instance)
    return _repl_instance


# Registrar ao importar
register_plugin(CodeExecutorPlugin())