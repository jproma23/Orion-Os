"""Mede o custo REAL de um comando com ACK, do Pi ate o Mega e de volta.

Este e o numero que decide se `comm.send` esperar ACK dentro do laco de eventos
e um problema ou nao. Se o ACK volta em poucos ms, esperar nao custa nada; se
custa dezenas de ms, uma rajada de joystick (o dashboard repete na casa dos
150ms) empilha e o operador ve "travou".

Usa LIGHT_ON/LIGHT_OFF: estao no vocabulario permitido e NAO movem o robo.

Roda no Pi com o orion-motion PARADO (precisa da serial livre).
Baud vem do orion.yaml - as ferramentas antigas tem 115200 fixo e falham em
silencio desde que o baud virou 250000 (2026-07-27).
"""
from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.communication.transport import SerialTransport
from orion.kernel.config import ConfigurationManager
from orion.kernel.event_bus import EventBus

RAIZ = Path(__file__).resolve().parent.parent
_conf = (
    ConfigurationManager(RAIZ / "config" / "orion.yaml").carregar().secao("communication")["hardware_core"]
)
PORTA = "/dev/ttyUSB0" if _conf.get("port", "auto") == "auto" else _conf["port"]
BAUD = _conf["baud_rate"]
N = 20


async def main() -> int:
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
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

    print(f"{N} comandos SEQUENCIAIS, cada um esperando o ACK do anterior:\n")
    tempos = []
    falhas = 0
    for i in range(N):
        comando = "LIGHT_ON" if i % 2 == 0 else "LIGHT_OFF"
        t0 = time.monotonic()
        try:
            await svc.send("hardware_core", {"comando": comando})
            dt = (time.monotonic() - t0) * 1000
            tempos.append(dt)
            barra = "#" * min(50, int(dt / 4))
            print(f"  {i+1:>2} {comando:<9} {dt:7.1f} ms  {barra}")
        except ErroComunicacao as e:
            falhas += 1
            print(f"  {i+1:>2} {comando:<9} FALHOU - {e}")

    await transporte.fechar()
    bus.parar()
    tarefa_bus.cancel()
    try:
        await tarefa_bus
    except asyncio.CancelledError:
        pass

    if tempos:
        tempos_ord = sorted(tempos)
        mediana = statistics.median(tempos)
        print(f"\n  mediana {mediana:.1f} ms | min {tempos_ord[0]:.1f} | max {tempos_ord[-1]:.1f}"
              f" | falhas {falhas}")
        print(f"  teto de comandos por segundo nesta cadencia: {1000/mediana:.1f}/s")
        print("\n  O joystick do dashboard repete na casa dos 150 ms (~6,7 comandos/s).")
        if mediana > 150:
            print("  >>> CADA comando custa MAIS que o intervalo de repeticao: a fila cresce"
                  " sem parar.")
        elif mediana > 60:
            print("  >>> Custo alto: rajada rapida ou qualquer retransmissao ja acumula atraso.")
        else:
            print("  >>> Custo baixo: esperar o ACK NAO explica sozinho o travamento.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
