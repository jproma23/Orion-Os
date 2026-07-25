"""Modulo de voz do Mission Core (EDR-0023).

Extrai de tools/conversar_fofao.py toda a montagem da voz: escolha do
microfone, os DOIS modelos Whisper (um transcreve o comando, outro fica
vigiando a palavra de ativacao), a sintese Piper, o portao de atividade
sonora (VAD) e o VoiceCore que amarra tudo.

Dono do microfone e do alto-falante (EDR-0023 s.3).

Sobre `processar_comando`: o modulo recebe um CALLABLE, nunca um outro
modulo. Quem casa a voz com a IA e o Boot Manager, que conhece os dois -
assim ModuloVoz continua sem saber que existe um ModuloMissao, e trocar a
IA por outra coisa nao exige tocar na voz.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from orion.kernel.event_bus import EventBus

logger = logging.getLogger("orion.voice.modulo")

ProcessarComando = Callable[[str], Awaitable[str]]

#: dito uma vez quando a voz sobe, para o usuario saber que pode falar.
SAUDACAO_PADRAO = "Oi! Pode falar comigo. É só me chamar de Fofão."


class ModuloVoz:
    nome = "voice"

    def __init__(
        self,
        event_bus: EventBus,
        conf_voz: dict[str, Any],
        processar_comando: ProcessarComando,
        frase_saudacao: str | None = SAUDACAO_PADRAO,
        nucleo: Any | None = None,
    ) -> None:
        # `nucleo` injetavel: os testes exercitam o ciclo de vida sem
        # microfone, sem Whisper e sem Piper instalados.
        self._event_bus = event_bus
        self._conf = conf_voz
        self._processar_comando = processar_comando
        self._frase_saudacao = frase_saudacao
        self._nucleo = nucleo
        self._sintetizador: Any | None = None
        self._tarefa: asyncio.Task | None = None

    async def iniciar(self) -> None:
        if self._nucleo is None:
            self._nucleo = await self._construir()
        self._tarefa = asyncio.create_task(self._nucleo.executar())
        if self._frase_saudacao and self._sintetizador is not None:
            # falar DEPOIS de a escuta subir: se falarmos antes, o robo pode
            # perder o usuario que responde logo em seguida.
            try:
                await self._sintetizador.falar(self._frase_saudacao)
            except Exception:  # noqa: BLE001
                # audio de saida quebrado nao pode derrubar a escuta - o robo
                # mudo ainda ouve e ainda responde pela tela (achado de
                # 2026-07-24: erro transitorio de saida derrubava tudo junto)
                logger.warning("Falha ao falar a saudacao - seguindo mudo", exc_info=True)
        logger.info("Voz ativa - diga a palavra de ativacao")

    async def _construir(self) -> Any:
        # imports locais: puxam sounddevice, faster-whisper e piper, pesados
        # e ausentes em maquina de desenvolvimento sem audio.
        from orion.voice.captura_audio import SeletorMicrofone
        from orion.voice.sintese import Sintetizador
        from orion.voice.vad import DetectorAtividadeSonora
        from orion.voice.voice_core import VoiceCore
        from orion.voice.wake_word import DetectorPalavraAtivacao

        logger.info("Escolhendo o melhor microfone (Cap 9 s.6)...")
        seletor = SeletorMicrofone(self._conf["microfones_candidatos_indices"])
        indice_mic = await seletor.escolher_melhor()

        # Whisper le o modelo do disco no construtor e bloqueia por dezenas de
        # segundos. Fora da thread do event loop, senao o boot inteiro
        # congela aqui e os heartbeats se perdem.
        transcritor = await asyncio.to_thread(
            _TranscritorComLog, self._conf["whisper_model"]
        )
        transcritor_ativacao = await asyncio.to_thread(
            _TranscritorComLog, self._conf["whisper_model_ativacao"]
        )

        self._sintetizador = Sintetizador(
            self._conf["piper_voice_path"],
            indice_dispositivo_saida=self._conf["saida_audio_indice"],
        )

        detector_atividade = None
        conf_vad = self._conf["vad"]
        if conf_vad["habilitado"]:
            detector_atividade = DetectorAtividadeSonora(
                fator_acima_do_ruido=conf_vad["fator_acima_do_ruido"],
                rms_minimo=conf_vad["rms_minimo"],
                janelas_de_historico=conf_vad["janelas_de_historico"],
            )
            logger.info("VAD ligado: o Whisper de vigilancia so roda com som acima do ruido")

        # A palavra vem do orion.yaml (regra 6). O proprio
        # DetectorPalavraAtivacao ja tolera erro fonetico por distancia de
        # edicao desde 2026-07-24 - o DetectorFuzzy que vivia dentro de
        # conversar_fofao.py virou duplicata (e com limiar diferente do da
        # biblioteca, o que so confundia) e nao veio para ca.
        detector_ativacao = DetectorPalavraAtivacao((self._conf["wake_word"],))

        return VoiceCore(
            event_bus=self._event_bus,
            indice_microfone=indice_mic,
            transcritor=transcritor,
            sintetizador=self._sintetizador,
            processar_comando=self._processar_comando,
            detector_palavra_ativacao=detector_ativacao,
            frase_ativacao="Oi? Pode falar!",
            transcritor_ativacao=transcritor_ativacao,
            detector_atividade=detector_atividade,
        )

    async def encerrar(self) -> None:
        # contrato do EDR-0023: nunca levanta. Solta o microfone mesmo que
        # algo ja tenha quebrado antes.
        if self._nucleo is not None:
            try:
                self._nucleo.parar()
            except Exception:  # noqa: BLE001
                logger.warning("Falha ao parar o VoiceCore", exc_info=True)
        if self._tarefa is not None:
            self._tarefa.cancel()
            try:
                await self._tarefa
            except BaseException:  # noqa: BLE001 - inclui CancelledError
                pass
            self._tarefa = None

    def esta_saudavel(self) -> bool:
        return self._tarefa is not None and not self._tarefa.done()


def _TranscritorComLog(modelo: str) -> Any:  # noqa: N802 - fabrica, nao classe
    """Transcritor que registra o volume (RMS) junto com o texto.

    Sem isso nao da para distinguir "microfone captando fraco" de "Whisper
    entendendo errado" - foi essa informacao que permitiu diagnosticar a
    palavra de ativacao falhando ao vivo em 2026-07-24.
    """
    from orion.voice.transcricao import Transcritor

    class TranscritorComLog(Transcritor):
        async def transcrever(self, audio) -> str:  # type: ignore[override]
            import numpy as np

            rms = float(np.sqrt(np.mean(np.asarray(audio, dtype="float64") ** 2)))
            texto = await super().transcrever(audio)
            logger.info("ouvi (rms=%.4f): %r", rms, texto)
            return texto

    return TranscritorComLog(modelo=modelo)
