"""Testes do orquestrador Voice Core (Cap 9 s.3-5) - maquina de estados e
eventos. Usa fakes no lugar de microfone/Whisper/Piper reais.
"""
import asyncio

import pytest

pytest.importorskip("numpy")
pytest.importorskip("sounddevice")
pytest.importorskip("faster_whisper")
pytest.importorskip("piper")

from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.voice.voice_core import EstadoVoz, VoiceCore  # noqa: E402
from orion.voice.wake_word import DetectorPalavraAtivacao  # noqa: E402


class TranscritorFalso:
    def __init__(self, textos: list[str]) -> None:
        self._textos = list(textos)

    async def transcrever(self, audio) -> str:
        return self._textos.pop(0) if self._textos else ""


class SintetizadorFalso:
    def __init__(self) -> None:
        self.falas: list[str] = []

    async def falar(self, texto: str) -> None:
        self.falas.append(texto)


async def _gravar_audio_falso(duracao: float):
    return object()


async def _rodar_bus(bus: EventBus) -> asyncio.Task:
    return asyncio.create_task(bus.iniciar())


@pytest.mark.asyncio
async def test_sem_palavra_de_ativacao_volta_para_idle():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)

    async def processar(texto):
        return "nao deveria chamar"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["oi, tudo bem"]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
    )

    processou = await voice.ciclo_uma_vez()

    assert processou is False
    assert voice.estado is EstadoVoz.IDLE

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_fluxo_completo_com_palavra_de_ativacao():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    for topico in (
        "voice.wake_detected",
        "voice.command_received",
        "voice.transcription_ready",
        "voice.response_started",
        "voice.response_finished",
    ):
        bus.subscribe(topico, lambda e, t=topico: eventos.append(t))

    async def processar(texto):
        assert texto == "que horas sao"
        return "sao dez horas"

    sintetizador = SintetizadorFalso()
    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["fofao", "que horas sao"]),
        sintetizador=sintetizador,
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
    )

    processou = await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert processou is True
    assert voice.estado is EstadoVoz.IDLE
    assert sintetizador.falas == ["sao dez horas"]
    assert eventos == [
        "voice.wake_detected",
        "voice.command_received",
        "voice.transcription_ready",
        "voice.response_started",
        "voice.response_finished",
    ]

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_erro_no_processamento_ainda_fala_uma_resposta():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)

    async def processar_com_erro(texto):
        raise RuntimeError("IA fora do ar")

    sintetizador = SintetizadorFalso()
    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["fofao", "oi"]),
        sintetizador=sintetizador,
        processar_comando=processar_com_erro,
        gravar_audio=_gravar_audio_falso,
    )

    await voice.ciclo_uma_vez()

    assert len(sintetizador.falas) == 1
    assert "problema" in sintetizador.falas[0].lower()

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_erro_de_audio_publica_voice_audio_error():
    from orion.voice.captura_audio import ErroAudio

    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("voice.audio_error", lambda e: eventos.append(e.dados))

    async def gravar_com_erro(duracao):
        raise ErroAudio("microfone desconectado")

    async def processar(texto):
        return "nao deveria chegar aqui"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso([]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=gravar_com_erro,
    )

    processou = await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert processou is False
    assert voice.estado is EstadoVoz.ERROR
    assert len(eventos) == 1

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_deteccao_de_ativacao_customizada():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)

    async def processar(texto):
        return "ok"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["ei robo", "teste"]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
        detector_palavra_ativacao=DetectorPalavraAtivacao(palavras_ativacao=("robo",)),
    )

    processou = await voice.ciclo_uma_vez()

    assert processou is True

    bus.parar()
    await tarefa
