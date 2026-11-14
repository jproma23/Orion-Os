"""Cancelar uma fala nao pode soltar a trava com o audio ainda tocando.

Achado C6-03 da auditoria de 2026-11-09: `Professor.parar()` e o "para"
falado cancelam a tarefa que esta em `Sintetizador.falar`. O cancelamento
saia do `async with self._trava_fala` na hora, com a thread do
`asyncio.to_thread` AINDA dentro de `sd.wait()`. A fala seguinte ("Ta bom,
parei.") entrava num segundo `sd.play()` simultaneo - o SEGV de 05/08 - e
`falando` virava False com o audio no ar (a escuta religava e ouvia a propria
voz).

Usa o Sintetizador REAL; troca so o piper e o sounddevice por dubles que
medem quantos `play` estao ativos ao mesmo tempo. Roda sem placa de som nem
modelo (os dubles entram em sys.modules quando o pacote real falta).
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time
import types

import pytest

np = pytest.importorskip("numpy")


class _SomFalso:
    """sounddevice de mentira: `wait` dura 0,5 s ou ate `stop`."""

    def __init__(self):
        self.trava = threading.Lock()
        self.ativos = 0
        self.pico = 0
        self.paradas = 0
        self._parar = threading.Event()

    def play(self, audio, samplerate=None, device=None):
        with self.trava:
            self.ativos += 1
            self.pico = max(self.pico, self.ativos)
        self._parar.clear()

    def wait(self):
        self._parar.wait(0.5)  # a frase "tocando"
        with self.trava:
            self.ativos -= 1

    def stop(self):
        self.paradas += 1
        self._parar.set()


@pytest.fixture
def sintese_com_som_falso(monkeypatch):
    som = _SomFalso()
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "sounddevice", types.ModuleType("sounddevice"))
    try:
        import piper  # noqa: F401
    except ImportError:
        piper_falso = types.ModuleType("piper")
        piper_falso.PiperVoice = type("PiperVoice", (), {"load": staticmethod(lambda c: object())})
        monkeypatch.setitem(sys.modules, "piper", piper_falso)
    # O reimport abaixo tambem religa o ATRIBUTO `orion.voice.sintese` do pacote,
    # que o delitem nao devolve: sem isto os testes seguintes faziam monkeypatch
    # na copia nova enquanto a classe usava a original (14/11, so no Pi/Notebook).
    import orion.voice as pacote_voz

    monkeypatch.setattr(
        pacote_voz, "sintese", sys.modules.get("orion.voice.sintese"), raising=False
    )
    monkeypatch.delitem(sys.modules, "orion.voice.sintese", raising=False)
    sintese = importlib.import_module("orion.voice.sintese")
    sd = sintese.sd
    monkeypatch.setattr(sd, "play", som.play, raising=False)
    monkeypatch.setattr(sd, "wait", som.wait, raising=False)
    monkeypatch.setattr(sd, "stop", som.stop, raising=False)
    monkeypatch.setattr(
        sd, "query_devices", lambda idx=None: {"default_samplerate": 22050}, raising=False
    )

    s = sintese.Sintetizador.__new__(sintese.Sintetizador)
    s._trava_fala = asyncio.Lock()
    s.falando = False
    s.fim_ultima_fala = float("-inf")
    s._indice_dispositivo_saida = None
    s._sintetizar = lambda texto: (np.zeros(10, dtype=np.int16), 22050)
    return s, som


@pytest.mark.asyncio
async def test_cancelar_fala_nao_deixa_dois_play_simultaneos(sintese_com_som_falso):
    s, som = sintese_com_som_falso
    aula = asyncio.create_task(s.falar("frase da aula"))  # Professor._conduzir
    await asyncio.sleep(0.1)
    aula.cancel()  # Professor.parar() -> self._tarefa.cancel()
    with pytest.raises(asyncio.CancelledError):
        await aula

    # Quando o cancelamento termina, o audio ja parou de verdade.
    assert som.ativos == 0, "a trava foi solta com a frase ainda tocando"
    assert s.falando is False
    assert som.paradas == 1, "cancelar tem que interromper o audio com sd.stop()"

    await s.falar("Ta bom, parei.")  # RESPOSTA_PARAR falada em seguida
    assert som.pico == 1, f"{som.pico} sd.play simultaneos - e o SEGV de 05/08"


@pytest.mark.asyncio
async def test_falando_so_desliga_quando_o_audio_acaba(sintese_com_som_falso):
    s, som = sintese_com_som_falso
    aula = asyncio.create_task(s.falar("frase da aula"))
    await asyncio.sleep(0.1)
    aula.cancel()
    await asyncio.sleep(0)  # o cancelamento chegou, mas a thread ainda toca
    # Enquanto houver audio no ar, a escuta tem que continuar surda.
    if som.ativos:
        assert s.falando is True
    with pytest.raises(asyncio.CancelledError):
        await aula
    assert s.falando is False and som.ativos == 0


@pytest.mark.asyncio
async def test_cancelar_durante_a_sintese_nem_comeca_a_tocar(sintese_com_som_falso):
    s, som = sintese_com_som_falso

    def _sintese_lenta(texto):
        time.sleep(0.3)  # Piper pensando
        return np.zeros(10, dtype=np.int16), 22050

    s._sintetizar = _sintese_lenta
    tarefa = asyncio.create_task(s.falar("uma frase comprida"))
    await asyncio.sleep(0.05)
    tarefa.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarefa
    await asyncio.sleep(0.4)  # tempo de sobra para a thread velha tocar
    assert som.pico == 0, "a frase cancelada na sintese nao pode ir ao alto-falante"
