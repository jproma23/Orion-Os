import asyncio
from orion.communication.service import ComunicacaoService
from orion.communication.transport import SerialTransport
from orion.kernel.event_bus import EventBus

PORTA = "/dev/ttyUSB1"

async def main():
    bus = EventBus()
    svc = ComunicacaoService("motion_core", bus)
    transporte = SerialTransport(PORTA, 115200)
    tarefa_bus = asyncio.create_task(bus.iniciar())
    print("Conectando (aguardando reset do Mega, ~2s)...")
    await transporte.conectar()
    svc.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)
    resp = await svc.request("hardware_core", {"comando": "WHO_ARE_YOU"}, timeout_s=3.0)
    print(f"Handshake ok: {resp.payload}")
    await svc.send("hardware_core", {"comando": "RADAR_SET_ANGLE", "angulo_graus": 90})
    print("Servo comandado para 90 graus (centro logico) - PODE SOLTAR O PARAFUSO DO HORN E REENCAIXAR AGORA.")
    print("O servo vai ficar parado nesta posicao ate voce apertar Ctrl+C ou o script encerrar sozinho em 120s.")
    await asyncio.sleep(120)
    bus.parar()
    await tarefa_bus
    await transporte.fechar()

asyncio.run(main())
