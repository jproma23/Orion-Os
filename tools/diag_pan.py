"""Diagnostico: capta a vista com pan em -60 e em +60 (tilt travado em 0).
Comparando as duas fotos da para saber se o pan move na horizontal (correto)
ou na vertical (canais trocados). Roda no Pi, precisa da serial livre.
"""
from __future__ import annotations

import asyncio
import subprocess

from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.communication.transport import SerialTransport
from orion.kernel.event_bus import EventBus

PORTA, BAUD = "/dev/ttyUSB0", 115200
NOTEBOOK = "jproma23@10.20.20.195"
PASTA_NB = "/tmp/orion_pano"
CAP = f"~/orion-os/.venv/bin/python ~/orion-os/tools/capturar_frame.py"


def capturar(nome: str) -> None:
    r = subprocess.run(["ssh", NOTEBOOK, f"{CAP} {PASTA_NB}/{nome} 0 1"],
                       capture_output=True, text=True, timeout=30)
    print(f"  {nome}: {(r.stdout or r.stderr).strip()}")


async def main() -> int:
    bus = EventBus()
    svc = ComunicacaoService("motion_core", bus)
    t = SerialTransport(PORTA, BAUD)
    await t.conectar()
    svc.adicionar_link("hardware_core", t, exigir_checksum_mensagem=False)
    await svc.request("hardware_core", {"comando": "WHO_ARE_YOU"}, timeout_s=3.0)
    try:
        for pan, nome in [(-60, "diag_L.jpg"), (60, "diag_R.jpg")]:
            try:
                await svc.send("hardware_core",
                               {"comando": "SET_PAN_TILT", "pan_graus": pan, "tilt_graus": 0})
                print(f"pan={pan} tilt=0")
            except ErroComunicacao as e:
                print(f"pan={pan} SEM ACK - {e}")
            await asyncio.sleep(2.0)
            capturar(nome)
    finally:
        try:
            await svc.send("hardware_core",
                           {"comando": "SET_PAN_TILT", "pan_graus": 0, "tilt_graus": 0})
        except ErroComunicacao:
            pass
        await t.fechar()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
