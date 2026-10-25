"""Modulo de voz do Mission Core (EDR-0023).

Extrai de tools/conversar_jarvis.py toda a montagem da voz: escolha do
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
from typing import Any
from collections.abc import Awaitable, Callable

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("orion.voice.modulo")

ProcessarComando = Callable[[str], Awaitable[str]]

#: dito uma vez quando a voz sobe, para o usuario saber que pode falar.
SAUDACAO_PADRAO = "Oi! Eu sou o Jarvis. Pode falar comigo."


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
        #: Ouvidos do ESP32 (2026-09-13): a classe ja existia testada desde
        #: 2026-07-29, com o config ouvidos_esp32.habilitado=true, mas nunca
        #: foi instanciada aqui - o robo continuava surdo procurando
        #: microfone local no Pi, que nunca teve nenhum. Guardado para
        #: encerrar() poder soltar a conexao.
        self._ouvidos: Any | None = None

    async def iniciar(self) -> None:
        if self._nucleo is None:
            # devolve None quando nao ha o que escutar - ver `_construir`
            self._nucleo = await self._construir()
        if self._nucleo is not None:
            self._tarefa = asyncio.create_task(self._nucleo.executar())
        # O chat digitado (webui) nunca passa pelo VoiceCore, entao a
        # resposta de texto so chega aqui por evento - ver
        # ModuloMissao._processar_chat. Pedido do dono, 2026-09-13: o Fofao
        # respondia por voz mesmo quando a pergunta era por voz, e digitar
        # devia ter o mesmo efeito.
        self._event_bus.subscribe("mission.conversa_resposta", self._ao_receber_resposta_chat)
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

    async def _ao_receber_resposta_chat(self, evento: Evento) -> None:
        # basta ter BOCA: sem nucleo (robo surdo, ver `iniciar()`) ele ainda
        # responde em voz alta o que foi digitado no chat.
        if self._nucleo is None and self._sintetizador is None:
            return
        texto = str(evento.dados.get("texto", "")).strip()
        if not texto:
            return
        # NAO fazer `await` direto aqui: o EventBus despacha um evento de
        # cada vez, esperando o handler terminar antes do proximo (ver
        # `EventBus._despachar`). Falar demora vários segundos (Piper +
        # playback) - segurar o dispatcher inteiro por isso travaria tela,
        # telemetria e qualquer outro evento no meio de uma fala. A fila
        # dentro de `Sintetizador.falar` (2026-08-05, mesma protecao contra
        # o SIGSEGV de duas falas simultaneas) ja garante que isto nao pisa
        # em outra fala em andamento.
        asyncio.create_task(self._falar_resposta_chat(texto))

    async def _falar_resposta_chat(self, texto: str) -> None:
        try:
            # O nucleo e preferido porque ele tambem para de escutar enquanto
            # fala (estado SPEAKING, o cancelamento de eco). Sem nucleo nao ha
            # escuta para pausar, e falar direto pelo sintetizador e o certo.
            if self._nucleo is not None:
                await self._nucleo.falar(texto)
            else:
                await self._sintetizador.falar(texto)
        except Exception:  # noqa: BLE001
            # mesmo raciocinio da saudacao: audio de saida quebrado nao pode
            # derrubar nada - o chat de texto ja respondeu na tela de qualquer
            # jeito, falar em voz alta e um extra.
            logger.warning("Falha ao falar a resposta do chat", exc_info=True)

    def _montar_sintetizador(self) -> Any:
        """So a FALA - nao toca em microfone, nem em Whisper, nem em rede."""
        from orion.voice.sintese import Sintetizador

        return Sintetizador(
            self._conf["piper_voice_path"],
            indice_dispositivo_saida=self._conf["saida_audio_indice"],
        )

    async def _construir(self) -> Any:
        # imports locais: puxam sounddevice, faster-whisper e piper, pesados
        # e ausentes em maquina de desenvolvimento sem audio.
        from orion.voice.captura_audio import ErroAudio, SeletorMicrofone
        from orion.voice.vad import DetectorAtividadeSonora
        from orion.voice.voice_core import VoiceCore
        from orion.voice.wake_word import DetectorPalavraAtivacao

        # O SINTETIZADOR PRIMEIRO, de proposito (2026-09-16): falar nao depende
        # de microfone nenhum. Montado aqui, ele sobrevive a uma falha da
        # escuta mais abaixo, e o robo continua respondendo em voz alta em vez
        # de ficar mudo por um defeito que nao e dele. Ver `iniciar()`.
        self._sintetizador = self._montar_sintetizador()

        gravar_audio = None
        indice_mic = -1  # nao usado quando gravar_audio vem dos ouvidos do ESP32

        conf_ouvidos = self._conf.get("ouvidos_esp32", {})
        if conf_ouvidos.get("habilitado"):
            from orion.voice.ouvidos_esp32 import OuvidosESP32

            self._ouvidos = OuvidosESP32(conf_ouvidos["host"], conf_ouvidos["porta"])
            await self._ouvidos.iniciar()
            # timeout_boot_s: quanto se espera o ESP32 aparecer no boot antes
            # de cair pro microfone local (config ja documentava a intencao;
            # so faltava este laco existir).
            prazo_s = conf_ouvidos.get("timeout_boot_s", 6.0)
            loop = asyncio.get_event_loop()
            limite = loop.time() + prazo_s
            while not self._ouvidos.conectado and loop.time() < limite:
                await asyncio.sleep(0.2)
            if self._ouvidos.conectado:
                logger.info(
                    "Ouvindo pelos ouvidos do ESP32 (%s:%d)",
                    conf_ouvidos["host"], conf_ouvidos["porta"],
                )
                gravar_audio = self._ouvidos.gravar
            else:
                logger.warning(
                    "Ouvidos do ESP32 nao apareceram em %.1fs - caindo pro microfone local",
                    prazo_s,
                )

        if gravar_audio is None:
            logger.info("Escolhendo o melhor microfone (Cap 9 s.6)...")
            seletor = SeletorMicrofone(self._conf["microfones_candidatos_indices"])
            try:
                indice_mic = await seletor.escolher_melhor()
            except ErroAudio as erro:
                # SEM MICROFONE O ROBO FICA SURDO, NAO MUDO (2026-09-16).
                #
                # Ate aqui este erro subia e derrubava o modulo de voz INTEIRO:
                # o Boot Manager anotava "Modulo voice indisponivel" e o Fofao
                # parava de falar qualquer coisa - inclusive a resposta do chat
                # DIGITADO, que nao tem nada a ver com microfone. Foi o que
                # aconteceu quando os ouvidos do ESP32 nao apareceram no boot e
                # o Pi, que nao tem microfone proprio, nao teve para onde cair.
                #
                # O sintetizador ja foi montado la em cima, entao a boca esta
                # de pe. Voltar None diz ao `iniciar()` que nao ha escuta para
                # rodar - nao ha o que ouvir, mas ha o que dizer.
                logger.warning(
                    "Sem microfone (%s) - o Jarvis segue SURDO, mas continua falando", erro
                )
                return None

        # Whisper le o modelo do disco no construtor e bloqueia por dezenas de
        # segundos. Fora da thread do event loop, senao o boot inteiro
        # congela aqui e os heartbeats se perdem.
        # Os nomes pelos quais o robo atende viram HOTWORDS do Whisper: sao
        # justamente as palavras que ele nao espera ouvir em portugues e erra
        # ("Fofao" -> 'Fulfao!'). Lido antes dos transcritores porque e deles
        # que os dois precisam.
        nomes = self._conf["wake_word"]
        if isinstance(nomes, str):
            nomes = [nomes]
        nomes = list(nomes)

        transcritor = await asyncio.to_thread(
            _TranscritorComLog, self._conf["whisper_model"], " ".join(nomes)
        )
        # O de vigilancia e o que MAIS precisa: a unica coisa que ele procura
        # nas janelas curtas e exatamente esses nomes.
        transcritor_ativacao = await asyncio.to_thread(
            _TranscritorComLog, self._conf["whisper_model_ativacao"], " ".join(nomes)
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
        # conversar_jarvis.py virou duplicata (e com limiar diferente do da
        # biblioteca, o que so confundia) e nao veio para ca.
        # `wake_word` aceita um nome ou uma LISTA de nomes (2026-09-17, pedido
        # do dono: "quero que ele responda por Jarvis também"). String continua
        # valendo - e o formato que a config usava e o que os testes montam.
        # (`nomes` ja foi normalizado la em cima, junto com as hotwords.)
        detector_ativacao = DetectorPalavraAtivacao(tuple(nomes))

        return VoiceCore(
            event_bus=self._event_bus,
            indice_microfone=indice_mic,
            transcritor=transcritor,
            sintetizador=self._sintetizador,
            processar_comando=self._processar_comando,
            detector_palavra_ativacao=detector_ativacao,
            exigir_palavra_ativacao=self._conf["exigir_palavra_ativacao"],
            frase_ativacao="Oi? Pode falar!",
            transcritor_ativacao=transcritor_ativacao,
            detector_atividade=detector_atividade,
            gravar_audio=gravar_audio,
        )

    async def encerrar(self) -> None:
        # contrato do EDR-0023: nunca levanta. Solta o microfone mesmo que
        # algo ja tenha quebrado antes.
        self._event_bus.unsubscribe("mission.conversa_resposta", self._ao_receber_resposta_chat)
        if self._ouvidos is not None:
            try:
                await self._ouvidos.encerrar()
            except Exception:  # noqa: BLE001
                logger.warning("Falha ao encerrar os ouvidos do ESP32", exc_info=True)
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
        # Saudavel = faz o que da para fazer. Sem microfone nao existe tarefa
        # de escuta (ver `_construir`), e o modulo ainda assim fala - reportar
        # doente ali colocaria o Boot Manager a reiniciar para sempre um modulo
        # que nao tem o que consertar. A surdez ja foi registrada no boot, com
        # o motivo, em vez de virar um alarme permanente sem explicacao.
        if self._tarefa is not None:
            return not self._tarefa.done()
        return self._sintetizador is not None


def _TranscritorComLog(modelo: str, hotwords: str | None = None) -> Any:  # noqa: N802
    """Transcritor que registra o volume (RMS) junto com o texto.

    Sem isso nao da para distinguir "microfone captando fraco" de "Whisper
    entendendo errado" - foi essa informacao que permitiu diagnosticar a
    palavra de ativacao falhando ao vivo em 2026-07-24.

    `hotwords` enviesa o decodificador para palavras que ele nao esperaria -
    e o que faz "Jarvis" ser reconhecido como Jarvis. O `Transcritor` ja
    aceitava e repassava o parametro para o faster-whisper desde sempre, mas
    NINGUEM nunca passava nada (2026-09-17): mais um parametro orfao, o mesmo
    padrao de codigo pronto e nao ligado que ja apareceu varias vezes neste
    projeto. Medido antes de ligar, com o microfone da tela: "Fofao" virava
    'Fulfao!' e era descartado pelo portao de confianca.
    """
    from orion.voice.transcricao import Transcritor

    class TranscritorComLog(Transcritor):
        async def transcrever(self, audio) -> str:  # type: ignore[override]
            import numpy as np

            rms = float(np.sqrt(np.mean(np.asarray(audio, dtype="float64") ** 2)))
            texto = await super().transcrever(audio)
            logger.info("ouvi (rms=%.4f): %r", rms, texto)
            return texto

    return TranscritorComLog(modelo=modelo, hotwords=hotwords)
