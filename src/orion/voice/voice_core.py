"""Maquina de estados e orquestrador do Voice Core (Cap 9 secao 3-5).

IDLE -> LISTENING -> WAKE_DETECTED -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE

Cada transicao publica `voice.status` (estado atual) e, nos pontos certos,
os eventos nomeados do Cap 9 s.5. `processar_comando` e injetado - quem
liga o Voice Core ao AI Manager/Mission Planner (Cap 7) passa o callback,
sem o Voice Core precisar conhecer Ollama/memoria diretamente.
"""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Awaitable, Callable

from orion.kernel.event_bus import EventBus, Prioridade
from orion.voice.captura_audio import ErroAudio, gravar_trecho
from orion.voice.sintese import Sintetizador
from orion.voice.transcricao import Transcritor
from orion.voice.vad import DetectorAtividadeSonora
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
        processar_comando_em_frases=None,
        detector_palavra_ativacao: DetectorPalavraAtivacao | None = None,
        duracao_janela_escuta_s: float = 3.0,
        duracao_comando_s: float = 5.0,
        gravar_audio: CallbackGravarAudio | None = None,
        frase_ativacao: str | None = None,
        transcritor_ativacao: Transcritor | None = None,
        detector_atividade: "DetectorAtividadeSonora | None" = None,
        assentamento_pos_fala_s: float = 0.9,
    ) -> None:
        self._event_bus = event_bus
        self._indice_microfone = indice_microfone
        self._transcritor = transcritor
        self._sintetizador = sintetizador
        self._processar_comando = processar_comando
        #: Caminho que entrega a resposta em FRASES (T5, 2026-07-30). Opcional:
        #: sem ele o ciclo usa `processar_comando` e fala de uma vez so, que e
        #: o comportamento anterior. Injetavel para os testes poderem exercitar
        #: os dois caminhos sem rede.
        self._processar_comando_em_frases = processar_comando_em_frases
        #: Levantado para DESCARTAR o que ainda nao foi falado.
        #:
        #: O precedente e do proprio projeto: o cliente do ESP32 precisou drenar
        #: o socket continuamente, senao o buffer entregava som de segundos
        #: atras. Falar por frases cria a mesma exposicao - se a conversa for
        #: interrompida no meio, as frases restantes JA ESTAO a caminho, e sem
        #: este sinal o robo responderia ao passado por um terceiro caminho.
        self._interromper_fala = asyncio.Event()
        self._detector = detector_palavra_ativacao or DetectorPalavraAtivacao()
        self._duracao_janela_escuta_s = duracao_janela_escuta_s
        self._duracao_comando_s = duracao_comando_s
        # Confirmacao audivel de que a palavra de ativacao foi ouvida (ex.:
        # "Oi?") - sem ela o usuario nao sabe QUANDO falar o comando e a
        # janela de gravacao captura silencio (visto ao vivo em 2026-07-18).
        self._frase_ativacao = frase_ativacao
        # Transcritor mais leve (ex.: Whisper "tiny") so para as janelas de
        # vigilancia da palavra de ativacao, que rodam sem parar - o
        # transcritor principal (mais preciso, mais lento) fica so para o
        # comando de verdade. None = usar o principal para tudo.
        self._transcritor_ativacao = transcritor_ativacao or transcritor
        # Portao barato na frente do Whisper de vigilancia (ver vad.py):
        # janela sem som acima do ruido de fundo nem chega a ser transcrita.
        # None = comportamento antigo (transcreve toda janela).
        self._detector_atividade = detector_atividade
        # injetavel para testar sem microfone/hardware real
        #: Quanto esperar depois de falar antes de voltar a gravar.
        #:
        #: 0,9 s cobre com folga o rastro de audio em transito do ESP32 pela
        #: WiFi (ver _falar_com_protecao). Custo: o robo demora isso a mais
        #: para voltar a escutar - imperceptivel numa conversa falada, e
        #: barato perto de ele responder a si mesmo. Se ainda escapar eco,
        #: SUBIR este numero antes de mexer em qualquer outra coisa.
        self._assentamento_pos_fala_s = assentamento_pos_fala_s
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

    async def _falar_com_protecao(self, texto: str) -> None:
        """Fala `texto`, tolerando falha da saida de audio - mesma classe
        de problema ja corrigida hoje do lado da entrada (microfone) e da
        camera: hardware real falha as vezes (PortAudio transitorio,
        dispositivo ocupado). Sem isso, um erro aqui derrubava o processo
        inteiro (voice_core, sentinela, avatar - tudo compartilha o mesmo
        `asyncio.run`), so porque o alto-falante falhou uma vez. Achado
        real da vistoria de codigo de 2026-07-24."""
        try:
            await self._sintetizador.falar(texto)
        except Exception as erro:
            logger.exception("Falha ao falar (saida de audio)")
            await self._event_bus.publish(
                "voice.audio_error", {"motivo": str(erro)}, prioridade=Prioridade.ALTA
            )
        finally:
            # ASSENTAMENTO DEPOIS DE FALAR (2026-08-02) - o robo estava
            # ouvindo a PROPRIA VOZ e tratando como fala do usuario.
            #
            # Capturado no log ao vivo:
            #     22:50:06  ouvi: 'Fofao!'
            #     22:50:15  ouvi: 'Como e que voce esta hoje?'  <- ele mesmo
            #     22:50:15  diario: pedido_ouvido {...}         <- como pedido DO DONO
            #
            # Nao adianta limpar o buffer: OuvidosEsp32.gravar() JA faz
            # self._bruto.clear() antes de cada gravacao. O problema e o
            # audio EM TRANSITO. Os ouvidos sao um ESP32 mandando 62,8 KB/s
            # por WiFi: quando o alto-falante cala, ainda existe audio ja
            # capturado pelo microfone e ainda viajando pela rede. O clear()
            # limpa o que chegou; o que esta no ar aterrissa logo depois e e
            # gravado como se fosse 'agora'.
            #
            # Esperar aqui deixa esse rastro CHEGAR - e ai o clear() que ja
            # existe o descarta. Uma linha no lugar certo, em vez de uma API
            # nova de descarte.
            #
            # Isto piorou junto com a persona nova (b44d57e): antes o robo
            # terminava com ponto final, agora termina com PERGUNTA - e
            # pergunta e o que mais parece comando quando volta pelo microfone.
            if self._assentamento_pos_fala_s > 0:
                await asyncio.sleep(self._assentamento_pos_fala_s)

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

        if self._detector_atividade is not None and not self._detector_atividade.tem_som(
            audio_janela
        ):
            # Silencio: poupa a transcricao inteira (era o maior custo de
            # CPU do Notebook - Whisper rodando continuamente, 2026-07-19).
            await self._definir_estado(EstadoVoz.IDLE)
            return False

        texto_janela = await self._transcritor_ativacao.transcrever(audio_janela)
        if not self._detector.verificar(texto_janela):
            await self._definir_estado(EstadoVoz.IDLE)
            return False

        await self._definir_estado(EstadoVoz.WAKE_DETECTED)
        await self._event_bus.publish("voice.wake_detected", {"texto_janela": texto_janela})

        # "Fofão, anda para frente" numa respirada so. Se a propria janela de
        # ativacao ja traz o comando, aproveita - em vez de responder "pode
        # falar" para um usuario que JA falou e gravar 3 segundos de silencio.
        #
        # Achado ao vivo em 2026-07-25: o usuario dizia a frase inteira, a
        # janela capturava "Fofão, anda para frente", o robo acordava, e a
        # janela seguinte (a do comando) pegava silencio porque ele ja tinha
        # terminado. Pedir para falar em dois tempos seria remendo - ninguem
        # fala com assistente assim.
        texto_comando = self._comando_na_janela(texto_janela)
        if texto_comando is None:
            if self._frase_ativacao:
                await self._falar_com_protecao(self._frase_ativacao)

            await self._definir_estado(EstadoVoz.TRANSCRIBING)
            try:
                audio_comando = await self._gravar_audio(self._duracao_comando_s)
            except ErroAudio as erro:
                await self._publicar_erro_audio(erro)
                return False

            texto_comando = await self._transcritor.transcrever(audio_comando)
        else:
            logger.info("comando veio junto com a ativacao: %r", texto_comando)
        await self._event_bus.publish("voice.command_received", {"texto": texto_comando})
        await self._event_bus.publish("voice.transcription_ready", {"texto": texto_comando})

        await self._definir_estado(EstadoVoz.THINKING)
        if self._processar_comando_em_frases is not None:
            await self._responder_falando_por_frases(texto_comando)
        else:
            try:
                resposta = await self._processar_comando(texto_comando)
            except Exception:
                logger.exception("Falha ao processar comando de voz")
                resposta = "Desculpa, tive um problema para processar isso."

            await self._definir_estado(EstadoVoz.SPEAKING)
            await self._event_bus.publish("voice.response_started", {"texto": resposta})
            await self._falar_com_protecao(resposta)
            await self._event_bus.publish("voice.response_finished", {"texto": resposta})

        await self._definir_estado(EstadoVoz.IDLE)
        return True

    def interromper_fala(self) -> None:
        """Descarta o que ainda nao foi falado da resposta em andamento.

        Quem interrompe a conversa (o operador, um comportamento de seguranca,
        uma nova ativacao) chama isto. Sem ele, as frases ja geradas seguiriam
        saindo pela caixa depois de a conversa ter virado outra coisa.
        """
        self._interromper_fala.set()

    async def _responder_falando_por_frases(self, texto_comando: str) -> None:
        """Fala a resposta conforme ela e gerada (T5.a, 2026-07-30).

        O ganho nao vem de gerar mais rapido: vem de parar de esperar o que
        ainda nao e preciso. Enquanto o modelo escreve a segunda frase, o robo
        ja esta dizendo a primeira - que e o que uma pessoa faz. Medido
        pareado, com o prompt real: 3,33 s -> 1,84 s ate o primeiro som (-45%).

        `voice.response_started` sai com a PRIMEIRA frase e nao com o texto
        inteiro, porque nesse instante o texto inteiro ainda nao existe. Quem
        consome esse evento e a legenda do avatar (avatar_server.py): ela passa
        a acompanhar a fala em vez de aparecer pronta no fim.
        `voice.response_finished` fecha com o texto completo, como antes.
        """
        self._interromper_fala.clear()
        partes: list[str] = []
        primeira = True
        try:
            async for frase in self._processar_comando_em_frases(texto_comando):
                if self._interromper_fala.is_set():
                    logger.info(
                        "fala interrompida - descartando %d frase(s) nao ditas",
                        1,
                    )
                    break
                if primeira:
                    await self._definir_estado(EstadoVoz.SPEAKING)
                    await self._event_bus.publish("voice.response_started", {"texto": frase})
                    primeira = False
                partes.append(frase)
                await self._falar_com_protecao(frase)
        except Exception:
            logger.exception("Falha ao processar comando de voz (em frases)")
            if not partes:
                # Nada foi dito ainda: da para salvar o turno com uma frase de
                # desculpa. Se ja falamos algo, calar aqui e melhor que emendar
                # um pedido de desculpas no meio de uma resposta que fazia
                # sentido.
                desculpa = "Desculpa, tive um problema para processar isso."
                await self._definir_estado(EstadoVoz.SPEAKING)
                await self._event_bus.publish("voice.response_started", {"texto": desculpa})
                partes.append(desculpa)
                await self._falar_com_protecao(desculpa)

        texto_completo = " ".join(partes)
        if primeira and not partes:
            # Nem uma frase saiu (resposta vazia): o estado precisa passar por
            # SPEAKING assim mesmo, senao quem escuta voice.status ve o robo
            # pular de THINKING direto para IDLE e conclui que ele ignorou.
            await self._definir_estado(EstadoVoz.SPEAKING)
        await self._event_bus.publish("voice.response_finished", {"texto": texto_completo})

    def _comando_na_janela(self, texto_janela: str) -> str | None:
        """O que sobrou depois da palavra de ativacao, se valer a pena.

        Devolve None quando a janela so tinha o chamado ("Fofão!"), para o
        fluxo normal seguir e gravar o comando em seguida.
        """
        import re

        palavras = re.findall(r"[\wÀ-ÿ]+", texto_janela)
        for i, palavra in enumerate(palavras):
            if not self._detector.verificar(palavra):
                continue
            resto = " ".join(palavras[i + 1 :]).strip()
            # Curto demais quase sempre e ruido da propria ativacao ("Fofão,
            # ah") ou pedaco de palavra. Exigir algo substancial evita mandar
            # lixo para a IA - e, pior, para o Mission Planner, que poderia
            # casar por acidente com um comando de movimento.
            return resto if len(resto) >= 6 else None
        return None

    async def executar(self) -> None:
        self._executando = True
        while self._executando:
            await self.ciclo_uma_vez()

    def parar(self) -> None:
        self._executando = False
