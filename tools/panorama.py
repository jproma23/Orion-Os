"""Panoramica por varredura de pan: o Pi gira o servo, o Notebook fotografa.

A webcam esta montada EM CIMA do pan/tilt, entao girar o pan gira a camera.
Para cada angulo de pan: manda SET_PAN_TILT ao Mega, espera assentar e pede
um frame ao Notebook (a camera esta la, /dev/video0). No fim junta tudo.

Roda no Raspberry Pi. Precisa do /dev/ttyUSB0 LIVRE - pare o motion_core antes
(ele segura a serial com exclusividade).

Uso: python3 tools/panorama.py
"""
from __future__ import annotations

import asyncio
import subprocess

from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.communication.transport import SerialTransport
from orion.kernel.event_bus import EventBus

PORTA = "/dev/ttyUSB0"
BAUD = 115200

NOTEBOOK = "jproma23@10.20.20.195"
PASTA_NB = "/tmp/orion_pano"          # onde os frames ficam no Notebook
PYTHON_NB = "~/orion-os/.venv/bin/python"   # o cv2 esta no venv, nao no python do sistema
HELPER_NB = "~/orion-os/tools/capturar_frame.py"

# esquerda -> direita; 20 graus por passo da bom overlap para a webcam casar
ANGULOS_PAN = [-60, -40, -20, 0, 20, 40, 60]
TILT = 0
ASSENTAR_S = 1.6                       # servo lento + tremor da torre


def capturar_no_notebook(indice: int) -> None:
    destino = f"{PASTA_NB}/frame_{indice:02d}.jpg"
    r = subprocess.run(
        ["ssh", NOTEBOOK, f"{PYTHON_NB} {HELPER_NB} {destino}"],
        capture_output=True, text=True, timeout=30,
    )
    print(f"  cam[{indice}]: {(r.stdout or r.stderr).strip()}")


async def main() -> int:
    bus = EventBus()
    svc = ComunicacaoService("motion_core", bus)
    transporte = SerialTransport(PORTA, BAUD)

    print(f"Conectando em {PORTA} (aguardando reset do Mega)...")
    await transporte.conectar()
    svc.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)
    resp = await svc.request("hardware_core", {"comando": "WHO_ARE_YOU"}, timeout_s=3.0)
    print(f"Handshake ok: {resp.payload}")

    subprocess.run(["ssh", NOTEBOOK, f"mkdir -p {PASTA_NB} && rm -f {PASTA_NB}/frame_*.jpg"],
                   check=True, timeout=15)

    try:
        for i, pan in enumerate(ANGULOS_PAN):
            try:
                await svc.send("hardware_core",
                               {"comando": "SET_PAN_TILT", "pan_graus": pan, "tilt_graus": TILT})
                print(f"pan={pan:>4} tilt={TILT}")
            except ErroComunicacao as e:
                print(f"pan={pan:>4} SEM ACK - {e}")
            await asyncio.sleep(ASSENTAR_S)
            capturar_no_notebook(i)
    finally:
        try:
            await svc.send("hardware_core",
                           {"comando": "SET_PAN_TILT", "pan_graus": 0, "tilt_graus": 0})
        except ErroComunicacao:
            pass
        await transporte.fechar()

    print("Varredura concluida, servos centralizados. Montando panoramica no Notebook...")
    subprocess.run(
        ["ssh", NOTEBOOK,
         f"cd ~/orion-os && .venv/bin/python tools/montar_panorama.py {PASTA_NB} {PASTA_NB}/panorama.jpg"],
        check=False, timeout=120,
    )
    print(f"Pronto. Panoramica em {NOTEBOOK}:{PASTA_NB}/panorama.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
