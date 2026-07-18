"""Monitor ao vivo dos ultrassons no Mega real (Cap 10 secao 10).

Fica consultando RETURN_STATUS em loop e imprime, a cada meio segundo, o
estado dos dois ultrassons: distancia frontal/traseira, se a leitura atual
e valida e os flags de diagnostico `echo_*_ja_visto_alto` (true se algum
pulso de ECHO chegou no pino do Mega desde o boot - e o que separa
"sensor mudo" de "sensor que responde as vezes").

Uso tipico durante depuracao fisica:
  1. Rode este script e deixe rodando.
  2. Passe a mao a ~10-20cm na frente de cada sensor - se o hardware
     estiver ok, a distancia muda e o flag vira SIM na hora.
  3. Com multimetro, meca TRIG/VCC/ECHO enquanto o script roda - o TRIG
     pulsa a cada 60ms, entao num multimetro comum ele aparece como uma
     tensao media pequena (~0,1-0,8V DC oscilando), nao 5V fixo.

Ctrl+C encerra a qualquer momento. Como no testar_pan_tilt.py, o uptime do
Mega e vigiado: se cair de repente, houve reset/brownout real.
"""
from __future__ import annotations

import asyncio

from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.communication.transport import SerialTransport
from orion.kernel.event_bus import EventBus

PORTA = "/dev/ttyUSB0"
BAUD = 115200
INTERVALO_S = 0.5


def _fmt(valor: object, valido: object) -> str:
    if valido:
        return f"{float(valor):6.1f}cm"
    return "  ---  "


async def main() -> int:
    bus = EventBus()
    svc = ComunicacaoService("motion_core", bus)
    transporte = SerialTransport(PORTA, BAUD)

    print(f"Conectando em {PORTA} @ {BAUD} (aguardando reset do Mega)...")
    await transporte.conectar()
    svc.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)

    resp = await svc.request("hardware_core", {"comando": "WHO_ARE_YOU"}, timeout_s=3.0)
    print(f"Handshake ok: {resp.payload}")
    print()
    print("Passe a mao na frente dos sensores. Ctrl+C para sair.")
    print("FRONTAL = TRIG 22 / ECHO 23   |   TRASEIRO = TRIG 26 / ECHO 27")
    print()

    ultimo_uptime: int | None = None
    falhas_seguidas = 0
    try:
        while True:
            try:
                resp = await svc.request(
                    "hardware_core", {"comando": "RETURN_STATUS"}, timeout_s=3.0
                )
            except ErroComunicacao as erro:
                falhas_seguidas += 1
                print(f"SEM RESPOSTA ({falhas_seguidas}x): {erro}")
                if falhas_seguidas >= 5:
                    print("5 falhas seguidas - verifique o cabo USB / o Mega.")
                    return 1
                await asyncio.sleep(INTERVALO_S)
                continue

            falhas_seguidas = 0
            p = resp.payload
            uptime = p.get("uptime_ms")
            if ultimo_uptime is not None and uptime is not None and uptime < ultimo_uptime:
                print(f"!!! RESET DETECTADO - uptime caiu de {ultimo_uptime}ms para {uptime}ms !!!")
            ultimo_uptime = uptime

            frontal = _fmt(p.get("distancia_frontal_cm"), p.get("distancia_frontal_valida"))
            traseira = _fmt(p.get("distancia_traseira_cm"), p.get("distancia_traseira_valida"))
            eco_f = "SIM" if p.get("echo_frontal_ja_visto_alto") else "nao"
            eco_t = "SIM" if p.get("echo_traseiro_ja_visto_alto") else "nao"
            print(
                f"frontal {frontal} (eco desde boot: {eco_f})  |  "
                f"traseiro {traseira} (eco desde boot: {eco_t})  |  "
                f"uptime {uptime}ms"
            )
            await asyncio.sleep(INTERVALO_S)
    except KeyboardInterrupt:
        print("\nEncerrado pelo usuario.")
    finally:
        await transporte.fechar()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
