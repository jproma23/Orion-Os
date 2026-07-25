"""Ponto de entrada do ORION OS.

Executa a sequencia de boot do Kernel (Cap 6, secao 4) e entao PERMANECE
VIVO atendendo o Event Bus, ate receber um pedido de parada (Ctrl+C, ou o
SIGTERM que o systemd manda no stop).

Antes desta versao o processo encerrava logo depois de publicar
system.ready - `python -m orion` era uma cerimonia de boot que terminava
em nada, e por isso o robo de verdade acabou vivendo dentro de
tools/conversar_fofao.py. Este arquivo e o primeiro passo para devolver o
sistema ao lugar que o Cap 6 define.

Modulos ausentes continuam tolerados (Cap 6 secao 8): o boot nao aborta
quando o Raspberry, o Arduino ou o banco nao respondem.
"""
from __future__ import annotations

import asyncio
import signal
import sys

from orion.kernel.boot import BootManager

VERSAO = "0.1.0"


def _pedir_aviso_de_parada(parar: asyncio.Event) -> None:
    """Faz SIGINT/SIGTERM apenas AVISAREM, em vez de matar o processo.

    Isso importa: o `systemctl stop` manda SIGTERM, e a morte crua no meio
    do caminho pularia o desligamento seguro do SistemaOrion - que precisa
    parar o watchdog, fechar a comunicacao com o Pi e drenar o Event Bus
    antes de sair (Cap 6).

    `add_signal_handler` existe no Unix, onde o robo roda. No Windows, usado
    so para desenvolvimento, ele levanta NotImplementedError; nesse caso
    seguimos sem tratador e o Ctrl+C ainda chega como KeyboardInterrupt.
    """
    loop = asyncio.get_running_loop()
    for numero in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(numero, parar.set)
        except (NotImplementedError, AttributeError):
            pass


async def _executar(simulado: bool) -> int:
    boot_manager = BootManager(simulado=simulado)
    sistema = await boot_manager.iniciar()

    parar = asyncio.Event()
    _pedir_aviso_de_parada(parar)
    print(f"ORION OS v{VERSAO} - system.ready publicado; rodando ate parada")

    try:
        await parar.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await sistema.encerrar()
    return 0


def main() -> int:
    sim = "--sim" in sys.argv
    try:
        return asyncio.run(_executar(sim))
    except KeyboardInterrupt:
        # Ctrl+C que escapou do tratador (Windows, ou antes do loop subir).
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
