"""Teste manual do hardware pan/tilt no Mega real (Cap 8 s.8 / Cap 10 s.2).

Fala direto com o Hardware Core pela porta serial, usando a mesma pilha de
protocolo do motion_core (sem precisar subir o processo inteiro) - util para
validar o servo fisico assim que montado, antes do Vision Core existir de
ponta a ponta.

Cada passo consulta RETURN_STATUS antes e depois do movimento e imprime o
uptime_ms do Mega - se o uptime cair de repente no meio do teste (sem a
gente ter reconectado), e sinal de reset/brownout real do Mega, nao so
perda de pacote na serial. Nunca aborta no meio: qualquer falha de ACK e
so avisada, o teste continua e sempre termina centralizando os servos.
"""
from __future__ import annotations

import asyncio

from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.communication.transport import SerialTransport
from orion.kernel.event_bus import EventBus

PORTA = "/dev/ttyUSB0"
BAUD = 115200

_ultimo_uptime = None


async def status(svc: ComunicacaoService) -> None:
    global _ultimo_uptime
    resp = await svc.request("hardware_core", {"comando": "RETURN_STATUS"}, timeout_s=3.0)
    uptime = resp.payload["uptime_ms"]
    if _ultimo_uptime is not None and uptime < _ultimo_uptime:
        print(f"    !!! RESET DETECTADO - uptime caiu de {_ultimo_uptime}ms para {uptime}ms !!!")
    _ultimo_uptime = uptime
    print(f"    status: estado={resp.payload['estado']} uptime_ms={uptime}")


async def mover(svc: ComunicacaoService, pan: float, tilt: float, rotulo: str) -> None:
    try:
        await svc.send("hardware_core", {"comando": "SET_PAN_TILT", "pan_graus": pan, "tilt_graus": tilt})
        print(f"OK  {rotulo}: pan={pan} tilt={tilt}")
    except ErroComunicacao as erro:
        print(f"SEM ACK {rotulo}: pan={pan} tilt={tilt} - {erro}")
    await status(svc)
    await asyncio.sleep(1.0)


async def main() -> int:
    bus = EventBus()
    svc = ComunicacaoService("motion_core", bus)
    transporte = SerialTransport(PORTA, BAUD)

    print(f"Conectando em {PORTA} @ {BAUD} (aguardando reset do Mega)...")
    await transporte.conectar()
    svc.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)

    resp = await svc.request("hardware_core", {"comando": "WHO_ARE_YOU"}, timeout_s=3.0)
    print(f"Handshake ok: {resp.payload}")
    await status(svc)

    try:
        await mover(svc, 0, 0, "centro")
        await mover(svc, 40, 0, "pan direita")
        await mover(svc, -40, 0, "pan esquerda")
        await mover(svc, 0, 0, "pan centro")
        await mover(svc, 0, 30, "tilt cima")
        await mover(svc, 0, -20, "tilt baixo")
        await mover(svc, 0, 0, "centro")
        await mover(svc, 40, 30, "pan+tilt direita/cima")
        await mover(svc, -40, -20, "pan+tilt esquerda/baixo")
        await mover(svc, 0, 0, "centro final")
    finally:
        # sempre tenta centralizar antes de fechar, mesmo se algo acima falhou
        try:
            await svc.send("hardware_core", {"comando": "SET_PAN_TILT", "pan_graus": 0, "tilt_graus": 0})
        except ErroComunicacao:
            pass
        await transporte.fechar()

    print("Teste concluido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
