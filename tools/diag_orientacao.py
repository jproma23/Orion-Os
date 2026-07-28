"""Descobre, por MEDICAO, se o quadro da camera esta espelhado na horizontal.

Por que existe: `vision.camera_frontal_espelhada` no orion.yaml decide se
`captura.py` aplica cv2.flip. Errar essa flag faz o pan/tilt FUGIR da pessoa
em vez de segui-la, e o erro nao aparece em teste nenhum - so na bancada, e
com o sintoma generico de "nao funciona". Em 2026-07-28 eu (assistente) troquei
a flag baseado em raciocinar sobre o TIPO de camera, e a foto do stream
contradisse. Isto aqui mede em vez de deduzir.

Metodo: com o pan em -30 e depois +30 (o eixo ja validado a olho pelo usuario:
positivo = direita do robo), fotografa o MESMO cenario nas duas pontas. Girando
para a direita, um cenario fixo anda para a ESQUERDA num quadro nao espelhado.
Se andar para a direita, o quadro esta espelhado.

Roda no Pi, com o orion-motion PARADO (precisa da serial livre).
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.communication.transport import SerialTransport
from orion.kernel.config import ConfigurationManager
from orion.kernel.event_bus import EventBus

RAIZ = Path(__file__).resolve().parent.parent
_conf = ConfigurationManager(RAIZ / "config" / "orion.yaml").carregar().secao("communication")[
    "arduino"
]
PORTA = "/dev/ttyUSB0" if _conf.get("port", "auto") == "auto" else _conf["port"]
BAUD = _conf["baud_rate"]
URL_SNAPSHOT = "http://127.0.0.1:8091/snapshot"
ASSENTAR_S = 3.0


def fotografar(destino: str) -> None:
    r = subprocess.run(
        ["curl", "-s", "--max-time", "8", URL_SNAPSHOT, "-o", destino],
        capture_output=True, text=True,
    )
    tamanho = Path(destino).stat().st_size if Path(destino).exists() else 0
    print(f"    foto -> {destino} ({tamanho} bytes){' FALHOU' if r.returncode else ''}")


async def main() -> int:
    bus = EventBus()
    svc = ComunicacaoService("motion_core", bus)
    transporte = SerialTransport(PORTA, BAUD)
    print(f"Conectando em {PORTA} @ {BAUD}...")
    await transporte.conectar()
    svc.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)
    await asyncio.sleep(3.0)
    for tentativa in range(1, 6):
        try:
            await svc.request("hardware_core", {"comando": "WHO_ARE_YOU"}, timeout_s=4.0)
            print(f"Handshake ok (tentativa {tentativa})\n")
            break
        except ErroComunicacao:
            await asyncio.sleep(2.0)
    else:
        print("MEGA NAO RESPONDEU.")
        await transporte.fechar()
        return 1

    try:
        for pan, nome in [(-30, "/tmp/orient_pan_menos30.jpg"), (30, "/tmp/orient_pan_mais30.jpg")]:
            await svc.send(
                "hardware_core",
                {"comando": "SET_PAN_TILT", "pan_graus": pan, "tilt_graus": 0},
            )
            lado = "DIREITA" if pan > 0 else "ESQUERDA"
            print(f">>> pan={pan:+d} ({lado} do robo), assentando {ASSENTAR_S}s")
            await asyncio.sleep(ASSENTAR_S)
            fotografar(nome)
    finally:
        try:
            await svc.send(
                "hardware_core", {"comando": "SET_PAN_TILT", "pan_graus": 0, "tilt_graus": 0}
            )
            print("\nvoltando ao centro (0,0)")
        except ErroComunicacao:
            pass
        await asyncio.sleep(1.0)
        await transporte.fechar()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
