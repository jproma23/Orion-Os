"""Maquina de estados e orquestrador do Voice Core (Cap 9 secao 3-5).

IDLE -> LISTENING -> WAKE_DETECTED -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE

Cada transicao publica `voice.status` (estado atual) e, nos pontos certos,
os eventos nomeados do Cap 9 s.5. `processar_comando` e injetado - quem
liga o Voice Core ao AI Manager/Mission Planner (Cap 7) passa o callback,
sem o Voice Core precisar conhecer Ollama/memoria diretamente.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Awaitable, Callable

from orion.kernel.event_bus import EventBus, Prioridade
from orion.voice.captura_audio import ErroAudio, gravar_trecho
from orion.voice.sintese import Sintetizador
from orion.voice.transcricao import Transcritor
from orion.voice.wake_word import DetectorPalavraAtivacao

logger = logging.getLogger("orion.voice.voice_core")

CallbackProcessarComando = Callable[[str], Awaitable[str]]
CallbackGravarAudio = Callable[[float], Awaitable[Any]]


class EstadoVoz(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    WAKE_DETECTED = "WAKE_DETECTED"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


class VoiceCore:
    def __init__(
        self,
        event_bus: EventBus,
        indice_microfone: int,
        transcritor: Transcritor,
        sintetizador: Sintetizador,
        processar_comando: CallbackProcessarComando,
        detector_palavra_ativacao: DetectorPalavraAtivacao | None = None,
        duracao_janela_escuta_s: float = 3.0,
        duracao_comando_s: float = 5.0,
        gravar_audio: CallbackGravarAudio | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._indice_microfone = indice_microfone
        self._transcritor = transcritor
        self._sintetizador = sintetizador
        self._processar_comando = processar_comando
        self._detector = detector_palavra_ativacao or DetectorPalavraAtivacao()
        self._duracao_janela_escuta_s = duracao_janela_escuta_s
        self._duracao_comando_s = duracao_comando_s
        # injetavel para testar sem microfone/hardware real
        self._gravar_audio = gravar_audio or (
            lambda duracao: gravar_trecho(self._indice_microfone, duracao)
        )
        self._estado = EstadoVoz.IDLE
        self._executando = False

    @property
    def estado(self) -> EstadoVoz:
        return self._estado

    async def _definir_estado(self, novo_estado: EstadoVoz) -> None:
        self._estado = novo_estado
        await self._event_bus.publish("voice.status", {"estado": novo_estado.value})

    async def _publicar_erro_audio(self, erro: ErroAudio) -> None:
        await self._definir_estado(EstadoVoz.ERROR)
        await self._event_bus.publish(
            "voice.audio_error", {"motivo": str(erro)}, prioridade=Prioridade.ALTA
        )

    async def ciclo_uma_vez(self) -> bool:
        """Um ciclo: escuta uma janela curta, verifica a palavra de
        ativacao e, se detectada, processa um comando completo ate falar a
        resposta. Retorna True se um comando foi processado (util em
        testes/logs), False se a janela nao tinha a palavra de ativacao."""
        await self._definir_estado(EstadoVoz.LISTENING)
        try:
            audio_janela = await self._gravar_audio(self._duracao_janela_escuta_s)
        except ErroAudio as erro:
            await self._publicar_erro_audio(erro)
            return False

        texto_janela = await self._transcritor.transcrever(audio_janela)
        if not self._detector.verificar(texto_janela):
            await self._definir_estado(EstadoVoz.IDLE)
            return False

        await self._definir_estado(EstadoVoz.WAKE_DETECTED)
        await self._event_bus.publish("voice.wake_detected", {"texto_janela": texto_janela})

        await self._definir_estado(EstadoVoz.TRANSCRIBING)
        try:
            audio_comando = await self._gravar_audio(self._duracao_comando_s)
        except ErroAudio as erro:
            await self._publicar_erro_audio(erro)
            return False

        texto_comando = await self._transcritor.transcrever(audio_comando)
        await self._event_bus.publish("voice.command_received", {"texto": texto_comando})
        await self._event_bus.publish("voice.transcription_ready", {"texto": texto_comando})

        await self._definir_estado(EstadoVoz.THINKING)
        try:
            resposta = await self._processar_comando(texto_comando)
        except Exception:
            logger.exception("Falha ao processar comando de voz")
            resposta = "Desculpa, tive um problema para processar isso."

        await self._definir_estado(EstadoVoz.SPEAKING)
        await self._event_bus.publish("voice.response_started", {"texto": resposta})
        await self._sintetizador.falar(resposta)
        await self._event_bus.publish("voice.response_finished", {"texto": resposta})

        await self._definir_estado(EstadoVoz.IDLE)
        return True

    async def executar(self) -> None:
        self._executando = True
        while self._executando:
            await self.ciclo_uma_vez()

    def parar(self) -> None:
        self._executando = False
