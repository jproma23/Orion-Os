"""Script pontual (nao versionado) so pra mandar SCAN_FRONT uma vez e ver o
resultado - confirmacao do arco calibrado 45-135 (2026-07-24)."""
from __future__ import annotations

import asyncio

from orion.communication.service import ComunicacaoService
from orion.communication.transport import SerialTransport
from orion.kernel.event_bus import EventBus

PORTA = "/dev/ttyUSB1"
BAUD = 115200


async def main() -> int:
    bus = EventBus()
    svc = ComunicacaoService("motion_core", bus)
    transporte = SerialTransport(PORTA, BAUD)

    resultado_pronto = asyncio.Event()
    leituras: list = []

    def ao_evento(evento) -> None:
        leituras.extend(evento.dados.get("leituras", []))
        resultado_pronto.set()

    bus.subscribe("motion.scan_complete", ao_evento)
    tarefa_bus = asyncio.create_task(bus.iniciar())

    print(f"Conectando em {PORTA} (aguardando reset do Mega, ~2s)...")
    await transporte.conectar()
    svc.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)

    resp = await svc.request("hardware_core", {"comando": "WHO_ARE_YOU"}, timeout_s=3.0)
    print(f"Handshake ok: {resp.payload}")

    print("Mandando SCAN_FRONT (arco calibrado 45-135)...")
    await svc.send("hardware_core", {"comando": "SCAN_FRONT"})

    try:
        await asyncio.wait_for(resultado_pronto.wait(), timeout=5.0)
        print("\nVarredura completa:")
        for leitura in leituras:
            valido = "ok" if leitura.get("valida") else "SEM LEITURA"
            print(f"  {leitura.get('angulo'):>3} graus: {leitura.get('distancia_cm'):6.1f}cm ({valido})")
    except asyncio.TimeoutError:
        print("Nao chegou motion.scan_complete em 5s.")

    bus.parar()
    await tarefa_bus
    await transporte.fechar()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
