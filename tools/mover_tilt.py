"""Move SO o tilt: +30 e depois -60, com o pan travado em 0.

Pedido do usuario na bancada (2026-07-28) para conferir a olho o sentido do
eixo vertical. Handshake com retentativa porque o Mega RESETA quando a serial
abre (DTR) e o bootloader leva alguns segundos - a versao sem retentativa
falhava com "Sem resposta em 3.0s" logo apos um replug.

O baud vem do config/orion.yaml, NAO de uma constante aqui. As ferramentas
de bancada tinham 115200 fixo e o baud virou 250000 em 2026-07-27 - todas
ficaram quebradas em silencio: o Mega continua transmitindo, mas chega lixo
binario e o WHO_ARE_YOU falha como se o hardware estivesse morto.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.communication.transport import SerialTransport
from orion.kernel.config import ConfigurationManager
from orion.kernel.event_bus import EventBus

RAIZ = Path(__file__).resolve().parent.parent
_conf_hardware_core = (
    ConfigurationManager(RAIZ / "config" / "orion.yaml").carregar().secao("communication")["hardware_core"]
)
#: `port: "auto"` na config significa descoberta por WHO_ARE_YOU (feita pelo
#: Motion Core). Aqui, com o Motion Core parado, cai no unico CH340 presente.
_porta_conf = _conf_hardware_core.get("port", "auto")
PORTA = "/dev/ttyUSB0" if _porta_conf == "auto" else _porta_conf
BAUD = _conf_hardware_core["baud_rate"]
PAUSA_S = 4.0


async def main() -> int:
    bus = EventBus()
    svc = ComunicacaoService("motion_core", bus)
    transporte = SerialTransport(PORTA, BAUD)
    print(f"Conectando em {PORTA}...")
    await transporte.conectar()
    svc.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)

    print("Esperando o Mega terminar de resetar...")
    await asyncio.sleep(3.0)
    for tentativa in range(1, 6):
        try:
            resp = await svc.request("hardware_core", {"comando": "WHO_ARE_YOU"}, timeout_s=4.0)
            print(f"Handshake ok na tentativa {tentativa}: {resp.payload}\n")
            break
        except ErroComunicacao as e:
            print(f"  tentativa {tentativa}/5 sem resposta ({e})")
            await asyncio.sleep(2.0)
    else:
        print("\nMEGA NAO RESPONDEU. Nao mando comando as cegas.")
        await transporte.fechar()
        return 1

    eixo = sys.argv[1] if len(sys.argv) > 1 else "tilt"
    if eixo not in ("pan", "tilt"):
        print(f"eixo invalido: {eixo!r} (use 'pan' ou 'tilt')")
        await transporte.fechar()
        return 2

    sentido = ("DIREITA", "ESQUERDA") if eixo == "pan" else ("CIMA", "BAIXO")
    sequencia = [(30, f"{eixo} +30  (contrato: {sentido[0]})"),
                 (-60, f"{eixo} -60  (contrato: {sentido[1]})")]
    print(f">>> so o eixo {eixo.upper()} varia; o outro fica travado em 0.\n")
    try:
        for valor, rotulo in sequencia:
            try:
                await svc.send(
                    "hardware_core",
                    {
                        "comando": "SET_PAN_TILT",
                        "pan_graus": valor if eixo == "pan" else 0,
                        "tilt_graus": valor if eixo == "tilt" else 0,
                    },
                )
                print(f">>> {rotulo}   - servo fisico recebe {90 - valor:.0f} graus")
            except ErroComunicacao as e:
                print(f">>> {rotulo}   SEM ACK - {e}")
            await asyncio.sleep(PAUSA_S)
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
