"""Plugin system_control - Controle de sistema: ps, kill, systemctl, journalctl, apps, launch, sysinfo."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import BasePlugin, PluginParameter, PluginResult, register_plugin, run_subprocess

logger = logging.getLogger("orion.mission.jarvis_plugins.system_control")


class SystemControlPlugin(BasePlugin):
    name = "system_control"
    description = """Controle de sistema: ps, kill, systemctl, journalctl, apps, launch, sysinfo"""
    parameters_schema = [
        PluginParameter(
            name="action",
            type="string",
            description="Acao: ps, kill, systemctl, journalctl, apps, launch, sysinfo",
            required=True,
            enum=["ps", "kill", "systemctl", "journalctl", "apps", "launch", "sysinfo"],
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
            return PluginResult.fail("Acao obrigatoria: ps, kill, systemctl, journalctl, apps, launch, sysinfo")

        try:
            method = getattr(self, f"_action_{action}", None)
            if not method:
                return PluginResult.fail(f"Acao desconhecida: {action}")
            return await method(**params)
        except Exception as e:
            logger.exception("Erro em system_control")
            return PluginResult.fail(f"Erro: {type(e).__name__}: {e}")


    async def _action_ps(self, filter: str = "", limit: int = 20, **_) -> PluginResult:
        try:
            cmd = ["ps", "aux", "--sort=-%cpu"]
            code, stdout, stderr = await run_subprocess(cmd, timeout=10)
            lines = stdout.strip().split("\n")
            processes = []
            for line in lines[1:limit+1]:
                if filter and filter.lower() not in line.lower():
                    continue
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    processes.append({
                        "user": parts[0], "pid": parts[1], "cpu": parts[2], "mem": parts[3],
                        "command": parts[10][:80]
                    })
            return PluginResult.ok(processes, metadata={"count": len(processes)})
        except Exception as e:
            return PluginResult.fail(f"Erro no ps: {e}")

    async def _action_kill(self, pid: int = 0, name: str = "", signal: int = 15, **_) -> PluginResult:
        try:
            if pid:
                cmd = ["kill", f"-{{signal}}", str(pid)]
            elif name:
                cmd = ["pkill", f"-{{signal}}", name]
            else:
                return PluginResult.fail("Forneca pid ou name")
            code, stdout, stderr = await run_subprocess(cmd, timeout=5)
            return PluginResult.ok(f"Sinal {{signal}} enviado", metadata={"code": code})
        except Exception as e:
            return PluginResult.fail(f"Erro no kill: {e}")

    async def _action_systemctl(self, command: str = "status", service: str = "", user: bool = False, **_) -> PluginResult:
        try:
            if not service:
                return PluginResult.fail("Servico obrigatorio")
            cmd = ["systemctl", "--user"] if user else ["systemctl"]
            cmd.extend([command, service])
            code, stdout, stderr = await run_subprocess(cmd, timeout=30)
            return PluginResult.ok(stdout or stderr, metadata={"command": command, "service": service, "code": code})
        except Exception as e:
            return PluginResult.fail(f"Erro no systemctl: {e}")

    async def _action_journalctl(self, unit: str = "", lines: int = 50, priority: str = "", since: str = "", **_) -> PluginResult:
        try:
            cmd = ["journalctl", "-n", str(lines), "--no-pager"]
            if unit:
                cmd.extend(["-u", unit])
            if priority:
                cmd.extend(["-p", priority])
            if since:
                cmd.extend(["--since", since])
            code, stdout, stderr = await run_subprocess(cmd, timeout=10)
            return PluginResult.ok(stdout, metadata={"unit": unit, "lines": lines})
        except Exception as e:
            return PluginResult.fail(f"Erro no journalctl: {e}")

    async def _action_apps(self, **_) -> PluginResult:
        try:
            import glob, os
            desktop_files = []
            for base in ["/usr/share/applications", "/var/lib/flatpak/exports/share/applications", f"{{os.path.expanduser('~')}}/.local/share/applications"]:
                for f in glob.glob(f"{{base}}/*.desktop"):
                    try:
                        with open(f) as fp:
                            content = fp.read()
                            name = ""
                            exec_cmd = ""
                            for line in content.split("\n"):
                                if line.startswith("Name="):
                                    name = line[5:]
                                elif line.startswith("Exec="):
                                    exec_cmd = line[5:]
                            if name:
                                desktop_files.append({"name": name, "exec": exec_cmd, "file": f})
                    except Exception:
                        continue
            return PluginResult.ok(desktop_files, metadata={"count": len(desktop_files)})
        except Exception as e:
            return PluginResult.fail(f"Erro listando apps: {e}")

    async def _action_launch(self, app: str = "", command: str = "", **_) -> PluginResult:
        try:
            if app:
                cmd = ["gtk-launch", app]
            elif command:
                cmd = command.split()
            else:
                return PluginResult.fail("Forneca app (nome .desktop) ou command")
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            return PluginResult.ok(f"Lancado: {{app or command}}")
        except Exception as e:
            return PluginResult.fail(f"Erro no launch: {e}")

    async def _action_sysinfo(self, **_) -> PluginResult:
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            net = psutil.net_io_counters()
            info = {
                "cpu_percent": cpu,
                "memory": {"total": mem.total, "available": mem.available, "percent": mem.percent},
                "disk": {"total": disk.total, "free": disk.free, "percent": disk.percent},
                "network": {"bytes_sent": net.bytes_sent, "bytes_recv": net.bytes_recv},
                "uptime": psutil.boot_time(),
            }
            return PluginResult.ok(info)
        except Exception as e:
            return PluginResult.fail(f"Erro no sysinfo: {e}")


register_plugin(SystemControlPlugin())
