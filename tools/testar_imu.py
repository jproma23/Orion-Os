"""Monitor ao vivo do MPU6050 no Mega real (Cap 10 secao 7).

A inclinacao e o impacto vem da TELEMETRY periodica do firmware (500ms) -
o RETURN_STATUS so carrega `imu_conectado`, sem os valores. O quadro
TELEMETRY chega ao Event Bus local como evento `comm.mensagem.telemetry`
(ComunicacaoService), entao o script assina esse topico e guarda o payload
mais recente. O RETURN_STATUS continua sendo consultado em loop para o
uptime (vigia de reset), igual ao testar_ultrassom.py.

Como testar na bancada:
  1. Rode este script com o robo parado e nivelado - a inclinacao deve
     ficar perto de 0 grau e estavel (ruido de 1-2 graus e normal).
  2. Incline o chassi devagar de lado: o valor sobe junto. Passando de
     20 graus (LIMITE_INCLINACAO_GRAUS) o flag INCLINACAO fica CRITICA e
     o SafetyManager do firmware para os motores.
  3. De um tapinha seco na estrutura: o flag IMPACTO pisca SIM (limiar de
     2.5 G). Se nunca piscar, confira se o MPU esta bem preso ao chassi.

Se `imu_conectado` vier false, o problema e I2C: confira SDA no pino 20,
SCL no 21, VCC em 5V (ou 3V3 conforme o modulo) e GND comum.

Ctrl+C encerra a qualquer momento.

ATENCAO: este tool abre /dev/ttyUSB0 direto - pare o servico do Motion
Core antes (systemctl --user stop orion-motion.service) ou a porta estara
ocupada. Com o servico rodando, prefira conferir os valores na interface
web (http://<pi>:8080/estado, secao "hardware").
"""
from __future__ import annotations

import argparse
import asyncio

from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.communication.transport import SerialTransport
from orion.kernel.event_bus import EventBus

PORTA = "/dev/ttyUSB0"
BAUD = 115200
INTERVALO_S = 0.5

# Mesmos limiares do firmware (imu_manager.h) - so para colorir a saida.
LIMITE_INCLINACAO_GRAUS = 20.0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limiar",
        type=float,
        metavar="G",
        help="grava o limiar de impacto em G (ex.: --limiar 3.0) e sai. "
             "Faixa valida: 1.05 a 7.5. Escolha por MEDIDA: rode sem "
             "argumento, provoque trancos reais e olhe a coluna 'pico'.",
    )
    parser.add_argument(
        "--calibrar",
        action="store_true",
        help="zera o vetor de referencia do IMU (robo parado e NIVELADO) e sai",
    )
    args = parser.parse_args()

    bus = EventBus()
    svc = ComunicacaoService("motion_core", bus)
    transporte = SerialTransport(PORTA, BAUD)

    # Payload da TELEMETRY mais recente (dict vazio ate a primeira chegar).
    ultima_telemetria: dict = {}

    def guardar_telemetria(evento) -> None:
        ultima_telemetria.clear()
        ultima_telemetria.update(evento.dados.get("payload") or {})

    bus.subscribe("comm.mensagem.telemetry", guardar_telemetria)
    tarefa_bus = asyncio.create_task(bus.iniciar())

    print(f"Conectando em {PORTA} @ {BAUD} (aguardando reset do Mega)...")
    await transporte.conectar()
    svc.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)

    resp = await svc.request("hardware_core", {"comando": "WHO_ARE_YOU"}, timeout_s=3.0)
    print(f"Handshake ok: {resp.payload}")
    print()

    if args.limiar is not None:
        resp = await svc.request(
            "hardware_core",
            {"comando": "SET_IMPACT_THRESHOLD", "limite_g": args.limiar},
            timeout_s=3.0,
        )
        p = resp.payload
        if p.get("ok"):
            print(f"Limiar de impacto gravado na EEPROM: {p.get('limite_g'):.2f} G")
        else:
            print(f"FALHOU ({p.get('erro')}) - limiar segue em {p.get('limite_g'):.2f} G")
        bus.parar()
        await tarefa_bus
        await transporte.fechar()
        return 0 if p.get("ok") else 1

    if args.calibrar:
        # Espera algumas leituras chegarem antes de congelar a referencia -
        # o firmware precisa ter atualizado o IMU pelo menos uma vez.
        await asyncio.sleep(1.0)
        resp = await svc.request(
            "hardware_core", {"comando": "CALIBRATE_IMU"}, timeout_s=3.0
        )
        p = resp.payload
        if p.get("ok"):
            print("Calibracao gravada na EEPROM. A posicao atual agora e o zero.")
            print("Rode o script sem --calibrar: deve ficar perto de 0.0 grau.")
            bus.parar()
            await tarefa_bus
            await transporte.fechar()
            return 0
        print(f"FALHOU: {p.get('erro')}")
        bus.parar()
        await tarefa_bus
        await transporte.fechar()
        return 1

    print("Incline o chassi e de tapinhas nele. Ctrl+C para sair.")
    print("MPU6050 = SDA 20 / SCL 21   |   limite de inclinacao: "
          f"{LIMITE_INCLINACAO_GRAUS:.0f} graus")
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

            t = ultima_telemetria
            if not t:
                print(f"aguardando primeira TELEMETRY...  |  uptime {uptime}ms")
                await asyncio.sleep(INTERVALO_S)
                continue

            if not t.get("imu_conectado"):
                print(f"IMU DESCONECTADO (I2C nao respondeu)  |  uptime {uptime}ms")
                await asyncio.sleep(INTERVALO_S)
                continue

            graus = float(t.get("inclinacao_graus", 0.0))
            critica = graus > LIMITE_INCLINACAO_GRAUS
            impacto = bool(t.get("impacto_detectado"))
            # pico_g = maior magnitude desde o quadro anterior. E o numero
            # que interessa para escolher o limiar: o valor instantaneo
            # quase nunca pega o topo de um tranco de 10-20ms.
            pico = float(t.get("pico_g", 0.0))
            atual = float(t.get("aceleracao_g", 0.0))
            print(
                f"inclin {graus:5.1f}gr {'CRIT' if critica else ' ok '}"
                f"  |  G atual {atual:4.2f}  pico {pico:5.2f}"
                f"  |  impacto {'SIM' if impacto else ' - '}"
                f" (>{float(t.get('limite_impacto_g', 0)):.1f}G)"
                f"  |  up {uptime}ms"
            )
            await asyncio.sleep(INTERVALO_S)
    except KeyboardInterrupt:
        print("\nEncerrado pelo usuario.")
    finally:
        bus.parar()
        await tarefa_bus
        await transporte.fechar()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
