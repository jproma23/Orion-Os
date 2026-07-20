"""Teste visual de UM eixo por vez, para confirmar a olho se pan/tilt estao
trocados. O outro eixo fica travado em 0.

Uso: python3 testar_so_pan.py [pan|tilt]   (padrao: pan)
  pan  -> so pan varia; a cabeca DEVERIA virar so para os lados
  tilt -> so tilt varia; a cabeca DEVERIA so subir e descer

Roda no Pi. Precisa do /dev/ttyUSB0 livre (parar orion-motion antes).
Movimentos lentos e exagerados de proposito, para dar tempo de observar.
"""
from __future__ import annotations

import asyncio
import sys

from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.communication.transport import SerialTransport
from orion.kernel.event_bus import EventBus

PORTA = "/dev/ttyUSB0"
BAUD = 115200
PAUSA_S = 2.5


async def main() -> int:
    eixo = sys.argv[1] if len(sys.argv) > 1 else "pan"
    if eixo == "tilt":
        # tilt tem menos alcance (-30..45); usa -25..40
        sequencia = [(0, "centro"), (-25, "vvv baixo"), (40, "^^^ cima"),
                     (-25, "vvv baixo"), (40, "^^^ cima"), (0, "centro (fim)")]
        esperado = "so SUBIR e DESCER, sem virar para os lados"
    else:
        sequencia = [(0, "centro"), (-60, "<<< esquerda"), (60, "direita >>>"),
                     (-60, "<<< esquerda"), (60, "direita >>>"), (0, "centro (fim)")]
        esperado = "so virar PARA OS LADOS, sem subir nem descer"

    def comando(valor: int) -> dict:
        # o eixo escolhido varia; o outro fica em 0
        pan = valor if eixo == "pan" else 0
        tilt = valor if eixo == "tilt" else 0
        return {"comando": "SET_PAN_TILT", "pan_graus": pan, "tilt_graus": tilt}
    bus = EventBus()
    svc = ComunicacaoService("motion_core", bus)
    transporte = SerialTransport(PORTA, BAUD)
    print(f"Conectando em {PORTA} (aguardando reset do Mega)...")
    await transporte.conectar()
    svc.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)
    resp = await svc.request("hardware_core", {"comando": "WHO_ARE_YOU"}, timeout_s=3.0)
    print(f"Handshake ok: {resp.payload}\n")
    print(f">>> TESTE '{eixo}': a cabeca deveria {esperado}.\n")

    try:
        for valor, rotulo in sequencia:
            try:
                await svc.send("hardware_core", comando(valor))
                print(f"  {eixo}={valor:>4}   {rotulo}")
            except ErroComunicacao as e:
                print(f"  {eixo}={valor:>4}   SEM ACK - {e}")
            await asyncio.sleep(PAUSA_S)
    finally:
        try:
            await svc.send("hardware_core",
                           {"comando": "SET_PAN_TILT", "pan_graus": 0, "tilt_graus": 0})
        except ErroComunicacao:
            pass
        await transporte.fechar()
    print("\nFim. Se virou so para os lados, pan e tilt estao corretos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
