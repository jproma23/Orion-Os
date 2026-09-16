"""Testes da ponte serial entre o Event Bus e o orion_face (2026-09-12).

Sem porta real: substitui serial.Serial por um fake que so acumula bytes
escritos, mesmo padrao usado em test_avatar_server.py pro socket HTTP.
"""
import asyncio
import contextlib
import json

import pytest
import pytest_asyncio
import serial as pyserial

from orion.display.ponte_serial import PonteSerial
from orion.kernel.event_bus import EventBus


class _SerialFake:
    def __init__(self):
        self.linhas: list[str] = []
        self.fechado = False

    def write(self, dados: bytes) -> int:
        self.linhas.append(dados.decode("utf-8"))
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


@pytest.mark.asyncio
async def test_porta_ausente_nao_levanta(monkeypatch, bus_rodando):
    def _explode(*a, **kw):
        raise pyserial.SerialException("porta nao existe")

    monkeypatch.setattr("orion.display.ponte_serial.serial.Serial", _explode)

    ponte = PonteSerial(bus_rodando, porta="/dev/nao-existe")
    await ponte.iniciar()  # nao pode levantar

    await bus_rodando.publish("voice.response_started", {})
    await asyncio.sleep(0.05)  # tambem nao pode levantar aqui
