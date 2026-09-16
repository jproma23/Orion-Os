"""Testes da ponte serial entre o Event Bus e o orion_face (2026-09-12).

Sem porta real: substitui serial.Serial por um fake que so acumula bytes
escritos, mesmo padrao usado em test_avatar_server.py pro socket HTTP.
"""
import asyncio
import contextlib
import json

import numpy as np
import pytest
import pytest_asyncio
import serial as pyserial

from orion.display.ponte_serial import PonteSerial
from orion.kernel.event_bus import EventBus


class _SerialFake:
    def __init__(self):
        self.linhas: list[str] = []
        self.binario = bytearray()
        self.fechado = False

    def write(self, dados: bytes) -> int:
        # os quadros de audio nao sao texto (comecam com 0xFE) - guarda os dois
        # tipos separados, que e exatamente o que o firmware faz do outro lado
        try:
            self.linhas.append(dados.decode("utf-8"))
        except UnicodeDecodeError:
            self.binario.extend(dados)
        return len(dados)

    def close(self) -> None:
        self.fechado = True


@pytest.fixture
def serial_fake(monkeypatch):
    fake = _SerialFake()
    monkeypatch.setattr("orion.display.ponte_serial.serial.Serial", lambda *a, **kw: fake)
    return fake


@pytest_asyncio.fixture
async def bus_rodando():
    bus = EventBus()
    # publish() so enfileira - precisa do loop de despacho rodando pra
    # entregar de fato aos assinantes (mesmo padrao de test_avatar_server.py).
    tarefa = asyncio.create_task(bus.iniciar())
    yield bus
    bus.parar()
    tarefa.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa


@pytest.mark.asyncio
async def test_repassa_inicio_e_fim_da_fala(serial_fake, bus_rodando):
    ponte = PonteSerial(bus_rodando, porta="/dev/fake")
    await ponte.iniciar()

    await bus_rodando.publish("voice.response_started", {})
    await bus_rodando.publish("voice.response_finished", {})
    await asyncio.sleep(0.05)

    assert json.loads(serial_fake.linhas[0]) == {"evento": "voice.response_started"}
    assert json.loads(serial_fake.linhas[1]) == {"evento": "voice.response_finished"}

    await ponte.encerrar()
    assert serial_fake.fechado


@pytest.mark.asyncio
async def test_repassa_pan_tilt_com_campos(serial_fake, bus_rodando):
    ponte = PonteSerial(bus_rodando, porta="/dev/fake")
    await ponte.iniciar()

    await bus_rodando.publish("motion.pan_tilt", {"pan": 15.0, "tilt": -5.0})
    await asyncio.sleep(0.05)

    assert json.loads(serial_fake.linhas[0]) == {
        "evento": "motion.pan_tilt",
        "pan": 15.0,
        "tilt": -5.0,
    }

    await ponte.encerrar()


def _desempacotar(quadro: bytes) -> bytes:
    """Desfaz o enquadramento do jeito que o firmware desfaz (net.cpp)."""
    assert quadro[0] == 0xFE, "todo quadro comeca com a marca"
    corpo = bytearray()
    i = 1
    while i < len(quadro):
        if quadro[i] == 0xFD:  # escape: o proximo byte volta com XOR 0x20
            corpo.append(quadro[i + 1] ^ 0x20)
            i += 2
        else:
            corpo.append(quadro[i])
            i += 1
    tam = (corpo[0] << 8) | corpo[1]
    payload = bytes(corpo[2:])
    assert len(payload) == tam, "o tamanho anunciado tem que bater com o payload"
    return payload


@pytest.mark.asyncio
async def test_fala_vai_para_a_tela_reamostrada(serial_fake, bus_rodando):
    ponte = PonteSerial(bus_rodando, porta="/dev/fake")
    await ponte.iniciar()

    # 1 segundo de PCM a 22050Hz (a taxa nativa do Piper)
    audio = np.zeros(22050, dtype=np.int16)
    audio[::2] = 1000  # algo diferente de silencio puro
    await bus_rodando.publish("voice.audio_pcm", {"audio": audio, "taxa": 22050})
    await asyncio.sleep(1.5)  # o envio e no ritmo da fala: ~1s

    assert serial_fake.binario, "nada chegou na serial"
    assert serial_fake.binario[0] == 0xFE

    # Soma o payload de todos os quadros: tem que dar 1 segundo a 16kHz,
    # nao os 22050 originais - senao a voz sairia acelerada e aguda.
    total = 0
    restante = bytes(serial_fake.binario)
    while restante:
        proximo = restante.find(b"\xfe", 1)
        quadro, restante = (
            (restante, b"") if proximo < 0 else (restante[:proximo], restante[proximo:])
        )
        total += len(_desempacotar(quadro))
    assert total == pytest.approx(16000 * 2, rel=0.02)

    await ponte.encerrar()


@pytest.mark.asyncio
async def test_audio_nao_bloqueia_os_outros_eventos(serial_fake, bus_rodando):
    """O EventBus e serializado: se o envio da fala fosse `await` direto no
    assinante, o rosto pararia de receber pan/tilt durante a fala inteira."""
    ponte = PonteSerial(bus_rodando, porta="/dev/fake")
    await ponte.iniciar()

    audio = np.zeros(16000 * 3, dtype=np.int16)  # 3 segundos de fala
    await bus_rodando.publish("voice.audio_pcm", {"audio": audio, "taxa": 16000})
    await bus_rodando.publish("motion.pan_tilt", {"pan": 1.0, "tilt": 2.0})
    await asyncio.sleep(0.2)  # muito antes dos 3s de fala terminarem

    assert any("motion.pan_tilt" in linha for linha in serial_fake.linhas)

    await ponte.encerrar()


@pytest.mark.asyncio
async def test_porta_ausente_nao_levanta(monkeypatch, bus_rodando):
    def _explode(*a, **kw):
        raise pyserial.SerialException("porta nao existe")

    monkeypatch.setattr("orion.display.ponte_serial.serial.Serial", _explode)

    ponte = PonteSerial(bus_rodando, porta="/dev/nao-existe")
    await ponte.iniciar()  # nao pode levantar

    await bus_rodando.publish("voice.response_started", {})
    await asyncio.sleep(0.05)  # tambem nao pode levantar aqui
