#!/usr/bin/env python3
"""Verifica o caminho REAL da fala pela tela, sem subir o boot inteiro.

Usa as mesmas classes de producao (EventBus, PonteSerial, Sintetizador) e a
mesma config - so nao carrega Whisper, IA e camera, que nao participam disso.

Uso: verificar_fala_tela.py "texto a falar"
"""
import asyncio
import sys

import yaml

from orion.display.ponte_serial import PonteSerial
from orion.kernel.event_bus import EventBus
from orion.voice.sintese import Sintetizador


async def main() -> None:
    texto = " ".join(sys.argv[1:]) or "Boa noite, João Paulo"

    conf = yaml.safe_load(open("config/orion.yaml", encoding="utf-8"))
    conf_voz = conf["voice"]
    print("falar_pela_tela =", conf_voz.get("falar_pela_tela"))

    bus = EventBus()
    despacho = asyncio.create_task(bus.iniciar())

    ponte = PonteSerial(bus)
    await ponte.iniciar()

    # o mesmo callback que orion/voice/modulo.py monta quando a flag esta ligada
    async def tocar_na_tela(audio, taxa):
        await bus.publish("voice.audio_pcm", {"audio": audio, "taxa": taxa})

    sintetizador = Sintetizador(
        conf_voz["piper_voice_path"],
        indice_dispositivo_saida=conf_voz["saida_audio_indice"],
        tocar_na_tela=tocar_na_tela if conf_voz.get("falar_pela_tela") else None,
    )

    await bus.publish("voice.response_started", {"texto": texto})
    print("falando:", texto)
    await sintetizador.falar(texto)
    # falar() volta assim que o PCM foi publicado; o envio no ritmo da fala
    # segue numa task da ponte - espera ela terminar antes de fechar a porta.
    await asyncio.sleep(len(texto) * 0.12 + 3)
    await bus.publish("voice.response_finished", {})
    await asyncio.sleep(0.3)

    await ponte.encerrar()
    bus.parar()
    despacho.cancel()
    print("fim")


asyncio.run(main())
