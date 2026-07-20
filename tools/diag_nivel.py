"""Captura a vista com pan=0 e tilt em varios valores, para achar qual deixa
a camera OLHANDO RETO (horizonte no centro). Usado para calibrar a pose de
repouso. Roda no Pi, serial livre.
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
CAP = "~/orion-os/.venv/bin/python ~/orion-os/tools/capturar_frame.py"
TILTS = [0, -10, -20]


async def main() -> int:
    bus = EventBus()
    svc = ComunicacaoService("motion_core", bus)
    t = SerialTransport(PORTA, BAUD)
    await t.conectar()
    svc.adicionar_link("hardware_core", t, exigir_checksum_mensagem=False)
    await svc.request("hardware_core", {"comando": "WHO_ARE_YOU"}, timeout_s=3.0)
    try:
        for tilt in TILTS:
            try:
                await svc.send("hardware_core",
                               {"comando": "SET_PAN_TILT", "pan_graus": 0, "tilt_graus": tilt})
                print(f"pan=0 tilt={tilt}")
            except ErroComunicacao as e:
                print(f"tilt={tilt} SEM ACK - {e}")
            await asyncio.sleep(2.0)
            nome = f"nivel_{tilt:+03d}.jpg"
            r = subprocess.run(["ssh", NOTEBOOK, f"{CAP} {PASTA_NB}/{nome} 0 1"],
                               capture_output=True, text=True, timeout=30)
            print(f"  {(r.stdout or r.stderr).strip()}")
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
