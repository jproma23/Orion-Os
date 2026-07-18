"""Ponto de entrada do ORION OS.

Fase 1: executa a sequencia de boot do Kernel (Cap 6, secao 4) ate publicar
system.ready. Fases futuras (Raspberry, Arduino, banco, IA, Vision, Motion
Core) ainda nao estao implementadas - o boot tolera esses modulos ausentes.
"""
import asyncio
import sys

from orion.kernel.boot import BootManager

VERSAO = "0.1.0"


async def _executar(simulado: bool) -> int:
    boot_manager = BootManager(simulado=simulado)
    sistema = await boot_manager.iniciar()
    try:
        print(f"ORION OS v{VERSAO} - system.ready publicado")
    finally:
        await sistema.encerrar()
    return 0


def main() -> int:
    sim = "--sim" in sys.argv
    return asyncio.run(_executar(sim))


if __name__ == "__main__":
    raise SystemExit(main())
