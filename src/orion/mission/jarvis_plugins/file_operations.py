"""Plugin File Operations - Leitura, escrita, busca, edicao e listagem de arquivos.

Capacidades:
- read: Le arquivo (texto ou binario)
- write: Escreve arquivo (cria diretorios se necessario)
- append: Anexa ao final do arquivo
- list: Lista arquivos/diretorios (com glob pattern)
- glob: Busca arquivos por padrao (recursivo)
- grep: Busca conteudo em arquivos (regex)
- edit: Substituicao de texto (search/replace)
- delete: Remove arquivo ou diretorio
- mkdir: Cria diretorio
- info: Metadados do arquivo (tamanho, modificacao, tipo)
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any

from .base import BasePlugin, PluginParameter, PluginResult, register_plugin, sanitize_path, truncate_output, run_subprocess

logger = logging.getLogger("orion.mission.jarvis_plugins.file_operations")


class FileOperationsPlugin(BasePlugin):
    name = "file_operations"
    description = "Operacoes completas de arquivo: read, write, append, list, glob, grep, edit, delete, mkdir, info"
    parameters_schema = [
        PluginParameter(name="operation", type="string", description="Operacao: read, write, append, list, glob, grep, edit, delete, mkdir, info", required=True, enum=["read", "write", "append", "list", "glob", "grep", "edit", "delete", "mkdir", "info"]),
        PluginParameter(name="path", type="string", description="Caminho do arquivo ou diretorio", required=False),
        PluginParameter(name="content", type="string", description="Conteudo para write/append/edit", required=False),
        PluginParameter(name="pattern", type="string", description="Padrao glob (ex: **/*.py) ou regex para grep", required=False),
        PluginParameter(name="search", type="string", description="Texto para buscar (edit/grep)", required=False),
        PluginParameter(name="replace", type="string", description="Texto para substituir (edit)", required=False),
        PluginParameter(name="recursive", type="boolean", description="Recursivo para list/glob/delete", required=False, default=True),
        PluginParameter(name="encoding", type="string", description="Encoding do arquivo (padrao: utf-8)", required=False, default="utf-8"),
        PluginParameter(name="max_results", type="number", description="Maximo de resultados (grep/glob)", required=False, default=100),
        PluginParameter(name="base_dir", type="string", description="Diretorio base para seguranca (padrao: home)", required=False),
    ]

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self._base_dir = Path(config.get("base_dir", Path.home())) if config else Path.home()

    async def execute(self, **kwargs) -> PluginResult:
        operation = kwargs.get("operation")
        path = kwargs.get("path")
        content = kwargs.get("content")
        pattern = kwargs.get("pattern")
        search = kwargs.get("search")
        replace = kwargs.get("replace")
        recursive = kwargs.get("recursive", True)
        encoding = kwargs.get("encoding", "utf-8")
        max_results = kwargs.get("max_results", 100)
        base_dir = Path(kwargs.get("base_dir")) if kwargs.get("base_dir") else self._base_dir

        if not operation:
            return PluginResult.fail("Operacao obrigatoria: read, write, append, list, glob, grep, edit, delete, mkdir, info")

        try:
            if operation == "read":
                return await self._read(path, encoding, base_dir)
            elif operation == "write":
                return await self._write(path, content, encoding, base_dir)
            elif operation == "append":
                return await self._append(path, content, encoding, base_dir)
            elif operation == "list":
                return await self._list(path, recursive, base_dir)
            elif operation == "glob":
                return await self._glob(pattern, recursive, max_results, base_dir)
            elif operation == "grep":
                return await self._grep(search, pattern, max_results, base_dir)
            elif operation == "edit":
                return await self._edit(path, search, replace, encoding, base_dir)
            elif operation == "delete":
                return await self._delete(path, recursive, base_dir)
            elif operation == "mkdir":
                return await self._mkdir(path, base_dir)
            elif operation == "info":
                return await self._info(path, base_dir)
            else:
                return PluginResult.fail(f"Operacao desconhecida: {operation}")
        except ValueError as e:
            return PluginResult.fail(str(e))
        except Exception as e:
            logger.exception("Erro em file_operations")
            return PluginResult.fail(f"Erro: {type(e).__name__}: {e}")

    def _resolve_path(self, path: str | None, base_dir: Path) -> Path:
        if not path:
            raise ValueError("Caminho (path) obrigatorio para esta operacao")
        return sanitize_path(path, base_dir)

    async def _read(self, path: str, encoding: str, base_dir: Path) -> PluginResult:
        target = self._resolve_path(path, base_dir)
        if not target.exists():
            return PluginResult.fail(f"Arquivo nao encontrado: {path}")
        if target.is_dir():
            return PluginResult.fail(f"Caminho e um diretorio: {path}")

        try:
            text = target.read_text(encoding=encoding)
            return PluginResult.ok(truncate_output(text), metadata={"size": target.stat().st_size, "path": str(target)})
        except UnicodeDecodeError:
            size = target.stat().st_size
            return PluginResult.ok(f"[Arquivo binario, {size} bytes]", metadata={"size": size, "binary": True, "path": str(target)})

    async def _write(self, path: str, content: str | None, encoding: str, base_dir: Path) -> PluginResult:
        target = self._resolve_path(path, base_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = content or ""
        target.write_text(content, encoding=encoding)
        return PluginResult.ok(f"Arquivo escrito: {path}", metadata={"size": len(content.encode(encoding)), "path": str(target)})

    async def _append(self, path: str, content: str | None, encoding: str, base_dir: Path) -> PluginResult:
        target = self._resolve_path(path, base_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = content or ""
        with target.open("a", encoding=encoding) as f:
            f.write(content)
        return PluginResult.ok(f"Conteudo anexado: {path}", metadata={"appended": len(content.encode(encoding)), "path": str(target)})

    async def _list(self, path: str | None, recursive: bool, base_dir: Path) -> PluginResult:
        target = self._resolve_path(path or ".", base_dir)
        if not target.exists():
            return PluginResult.fail(f"Caminho nao encontrado: {path}")

        items = []
        if recursive:
            for item in target.rglob("*"):
                if item == target:
                    continue
                rel = item.relative_to(target)
                items.append({
                    "path": str(rel),
                    "type": "dir" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else 0,
                })
                if len(items) >= 500:
                    break
        else:
            for item in target.iterdir():
                items.append({
                    "path": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else 0,
                })

        return PluginResult.ok(items, metadata={"count": len(items), "path": str(target)})

    async def _glob(self, pattern: str | None, recursive: bool, max_results: int, base_dir: Path) -> PluginResult:
        if not pattern:
            return PluginResult.fail("Padrao (pattern) obrigatorio para glob")
        target = base_dir
        matches = []
        try:
            if recursive:
                for item in target.rglob(pattern):
                    if item.is_file():
                        matches.append(str(item.relative_to(target)))
                        if len(matches) >= max_results:
                            break
            else:
                for item in target.glob(pattern):
                    if item.is_file():
                        matches.append(str(item.relative_to(target)))
        except Exception as e:
            return PluginResult.fail(f"Erro no glob: {e}")

        return PluginResult.ok(matches, metadata={"count": len(matches), "pattern": pattern})

    async def _grep(self, search: str | None, pattern: str | None, max_results: int, base_dir: Path) -> PluginResult:
        if not search:
            return PluginResult.fail("Texto de busca (search) obrigatorio para grep")
        
        file_pattern = pattern or "**/*"
        matches = []
        count = 0
        
        try:
            regex = re.compile(search)
            for file_path in base_dir.rglob(file_pattern):
                if not file_path.is_file():
                    continue
                if file_path.suffix in {".pyc", ".pyo", ".so", ".bin", ".exe", ".dll", ".jpg", ".png", ".gif", ".pdf", ".zip", ".gz", ".tar"}:
                    continue
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                    for i, line in enumerate(text.splitlines(), 1):
                        if regex.search(line):
                            matches.append({
                                "file": str(file_path.relative_to(base_dir)),
                                "line": i,
                                "content": line.strip()[:200],
                            })
                            count += 1
                            if count >= max_results:
                                break
                except Exception:
                    continue
                if count >= max_results:
                    break
        except re.error as e:
            return PluginResult.fail(f"Regex invalido: {e}")

        return PluginResult.ok(matches, metadata={"count": len(matches), "search": search})

    async def _edit(self, path: str, search: str | None, replace: str | None, encoding: str, base_dir: Path) -> PluginResult:
        if search is None:
            return PluginResult.fail("Texto de busca (search) obrigatorio para edit")
        target = self._resolve_path(path, base_dir)
        if not target.exists():
            return PluginResult.fail(f"Arquivo nao encontrado: {path}")

        text = target.read_text(encoding=encoding)
        if search not in text:
            return PluginResult.fail(f"Texto nao encontrado no arquivo: {search[:50]}...")

        new_text = text.replace(search, replace or "")
        if new_text == text:
            return PluginResult.fail("Nenhuma substituicao feita (texto ja igual)")

        target.write_text(new_text, encoding=encoding)
        replacements = text.count(search)
        return PluginResult.ok(f"{replacements} substituicoes feitas em {path}", metadata={"replacements": replacements, "path": str(target)})

    async def _delete(self, path: str, recursive: bool, base_dir: Path) -> PluginResult:
        target = self._resolve_path(path, base_dir)
        if not target.exists():
            return PluginResult.fail(f"Caminho nao encontrado: {path}")

        if target.is_dir():
            if recursive:
                shutil.rmtree(target)
                return PluginResult.ok(f"Diretorio removido: {path}")
            else:
                try:
                    target.rmdir()
                    return PluginResult.ok(f"Diretorio vazio removido: {path}")
                except OSError:
                    return PluginResult.fail(f"Diretorio nao vazio (use recursive=true): {path}")
        else:
            target.unlink()
            return PluginResult.ok(f"Arquivo removido: {path}")

    async def _mkdir(self, path: str, base_dir: Path) -> PluginResult:
        target = self._resolve_path(path, base_dir)
        target.mkdir(parents=True, exist_ok=True)
        return PluginResult.ok(f"Diretorio criado: {path}", metadata={"path": str(target)})

    async def _info(self, path: str, base_dir: Path) -> PluginResult:
        target = self._resolve_path(path, base_dir)
        if not target.exists():
            return PluginResult.fail(f"Caminho nao encontrado: {path}")

        st = target.stat()
        info = {
            "path": str(target),
            "name": target.name,
            "type": "dir" if target.is_dir() else "file" if target.is_file() else "other",
            "size": st.st_size,
            "mode": oct(st.st_mode),
            "modified": st.st_mtime,
            "created": st.st_ctime,
            "readonly": not os.access(target, os.W_OK),
        }
        return PluginResult.ok(info, metadata={"path": str(target)})


register_plugin(FileOperationsPlugin())