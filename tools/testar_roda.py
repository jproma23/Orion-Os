"""Bancada: gira UMA roda e le os dois fios do encoder (TEST_WHEEL).

Criado em 2026-10-25, quando a fiacao do L298N e dos encoders foi refeita
(pins.h) e nada dela tinha sido visto girar. Responde quatro perguntas por
roda, sem PID no meio para esconder erro de ligacao:

  1. o par de IN move a roda que o pins.h diz que move?
  2. o canal A (amarelo) conta?            -> bordas_a e pcnt_a > 0
  3. o canal B (verde) conta?              -> bordas_b > 0
  4. a ordem A/B inverte com o sentido?    -> subida_a_com_b_baixo/alto troca

So com as RODAS NO AR. O orion-motion tem que estar PARADO (a serial e dele):
    systemctl --user stop orion-motion
    .venv/bin/python tools/testar_roda.py
    systemctl --user start orion-motion
"""
from __future__ import annotations

import argparse
import json
import time

import serial

from _serial_bancada import BAUD, PORTA
from orion.communication.framing import DecodificadorSerial, codificar_serial
from orion.communication.protocol import Mensagem, TipoMensagem


def _comando(porta: serial.Serial, dec: DecodificadorSerial, payload: dict, espera_s: float = 3.0) -> dict | None:
    msg = Mensagem.nova(TipoMensagem.COMMAND, "motion_core", "hardware_core", payload)
    porta.write(codificar_serial(msg.to_bytes()))
    limite = time.monotonic() + espera_s
    while time.monotonic() < limite:
        for quadro in dec.alimentar_bytes(porta.read(porta.in_waiting or 1)):
            try:
                dados = json.loads(quadro)
            except ValueError:
                continue
            if dados.get("tipo") == "RESPONSE" and dados.get("id_referencia") == msg.id:
                return dados["payload"]
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--potencia", type=float, default=40.0, help="%% de PWM (padrao 40)")
    ap.add_argument("--duracao-ms", type=int, default=800)
    args = ap.parse_args()

    with serial.Serial(PORTA, BAUD, timeout=0.05) as porta:
        time.sleep(0.3)
        porta.reset_input_buffer()
        dec = DecodificadorSerial()
        for lado in ("esq", "dir"):
            for sentido in (1, -1):
                r = _comando(porta, dec, {"comando": "TEST_WHEEL", "lado": lado, "sentido": sentido,
                                          "potencia_percent": args.potencia, "duracao_ms": args.duracao_ms})
                print(f"{lado} sentido {sentido:+d}: {json.dumps(r) if r else "SEM RESPOSTA"}")
                time.sleep(0.8)
        _comando(porta, dec, {"comando": "STOP"}, espera_s=0.5)


if __name__ == "__main__":
    main()
