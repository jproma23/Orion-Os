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
from typing import Any
from collections.abc import Awaitable, Callable

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
        detector_atividade: DetectorAtividadeSonora | None = None,
        assentamento_pos_fala_s: float = 0.9,
        silencios_para_fechar_sessao: int = 2,
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
        #: Quantas janelas de silencio seguidas fecham a conversa.
        #:
        #: 2 janelas de comando (5 s cada) = ~10 s de silencio. Curto o
        #: bastante para o robo nao ficar escutando a casa inteira depois
        #: que a pessoa saiu, e longo o bastante para ela pensar antes de
        #: responder sem ser expulsa da conversa.
        self._silencios_para_fechar_sessao = silencios_para_fechar_sessao
        self._gravar_audio = gravar_audio or (
            lambda duracao: gravar_trecho(self._indice_microfone, duracao)
        )
        self._estado = EstadoVoz.IDLE
        self._executando = False
        #: True enquanto a fonte de audio esta falhando. Guarda a BORDA para
        #: o log nao repetir a mesma linha a cada ciclo - ver
        #: `_publicar_erro_audio`.
        self._audio_em_falha = False
        #: A ultima coisa que ELE disse, para reconhecer a propria voz quando
        #: ela volta pelo microfone - ver `_eh_eco_da_propria_fala`.
        self._ultima_fala = ""

    @property
    def estado(self) -> EstadoVoz:
        return self._estado

    async def _definir_estado(self, novo_estado: EstadoVoz) -> None:
        self._estado = novo_estado
        await self._event_bus.publish("voice.status", {"estado": novo_estado.value})

    async def _publicar_erro_audio(self, erro: ErroAudio) -> None:
        # Loga na BORDA (ficou surdo / voltou a ouvir), nao a cada ciclo: o
        # ciclo repete a cada ~7s e em 2026-08-14 a surdez durou 3h35, o que
        # seriam ~1800 linhas iguais num log que ainda nao tem rotacao. Mesmo
        # padrao do `_safe_mode_ativo` e do aviso de corpo mudo no Motion Core.
        #
        # Existe porque naquele dia o `journalctl` do avatar nao tinha UMA
        # linha sobre a surdez - o unico sinal era um evento que nem
        # atravessava o link. Quem for depurar isto de novo olha o log
        # primeiro; ele tem que contar.
        if not self._audio_em_falha:
            self._audio_em_falha = True
            logger.warning("audio de entrada FALHOU - o robo esta surdo: %s", erro)
        await self._definir_estado(EstadoVoz.ERROR)
        await self._event_bus.publish(
            "voice.audio_error", {"motivo": str(erro)}, prioridade=Prioridade.ALTA
        )

    def _marcar_audio_ok(self) -> None:
        """Fecha a borda aberta por `_publicar_erro_audio`. Chamado quando uma
        janela de escuta chega inteira - a unica prova de que a fonte voltou."""
        if self._audio_em_falha:
            self._audio_em_falha = False
            logger.info("audio de entrada voltou - o robo esta ouvindo de novo")

    async def falar(self, texto: str) -> None:
        """Fala um texto que NAO veio do ciclo de voz - hoje, a resposta do
        chat da webui (2026-08-03).

        Existe para haver UM caminho de fala, e nao dois. Chamar o
        sintetizador direto pularia o assentamento pos-fala abaixo, que e o
        que impede o robo de ouvir a propria voz pelos microfones do ESP32 e
        trata-la como pedido do dono (achado de 2026-08-02).

        RESOLVIDO EM 2026-08-05 - a nota antiga aqui dizia que nao havia
        trava contra falar por cima de outra resposta, e que a janela era
        estreita. A janela nao era estreita: ela derrubava o processo inteiro
        com SIGSEGV, 10 vezes em 7 dias, porque duas falas simultaneas usam o
        PiperVoice e o stream global do sounddevice ao mesmo tempo. A trava
        mora em `Sintetizador.falar` (uma so, no ponto por onde TODAS as falas
        passam - inclusive as que nao vem por aqui, como a saudacao e a
        entrega de recado). Ela atrasa a segunda fala, nunca a descarta.
        """
        await self._falar_com_protecao(texto)

    async def _falar_com_protecao(self, texto: str) -> None:
        """Fala `texto`, tolerando falha da saida de audio - mesma classe
        de problema ja corrigida hoje do lado da entrada (microfone) e da
        camera: hardware real falha as vezes (PortAudio transitorio,
        dispositivo ocupado). Sem isso, um erro aqui derrubava o processo
        inteiro (voice_core, sentinela, avatar - tudo compartilha o mesmo
        `asyncio.run`), so porque o alto-falante falhou uma vez. Achado
        real da vistoria de codigo de 2026-07-24."""
        # INFO, e nao debug: o log contava so o lado da ENTRADA ("ouvi
        # (rms=...): 'Javis'") e ficava mudo sobre a SAIDA. Em 2026-09-19 o
        # robo foi chamado, a sessao abriu e encerrou por silencio, e nao
        # havia uma linha no log que respondesse "ele falou?". Aqui e o ponto
        # unico por onde TODAS as falas passam - resposta, saudacao, recado -,
        # entao uma linha so cobre todas.
        logger.info("falando: %.100s", texto)
        # Guardado ANTES de falar: se a sintese falhar no meio, parte do audio
        # pode ter saido mesmo assim, e o eco desse pedaco tem que ser
        # reconhecido do mesmo jeito.
        self._ultima_fala = texto
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
        self._marcar_audio_ok()

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

        # O MESMO portao da sessao, aqui tambem (2026-09-20). Ate hoje este
        # caminho nao olhava o que tinha transcrito, e "acordou mas nao falou
        # nada" virava uma chamada de IA com a fala do usuario em branco - que
        # o Gemini recusa com 400 (ver `_motivo_para_descartar`).
        #
        # E NAO SE VOLTA PARA O IDLE: quem disse a palavra de ativacao QUER
        # falar, so nao chegou a tempo na janela de 3 s. Cair fora aqui o
        # obrigaria a dizer "Oi javis" de novo, que e exatamente o "falar em
        # dois tempos" que o bloco acima evita. Entao pula-se so a resposta e
        # entra-se na sessao, que tem a paciencia certa para esperar a fala.
        motivo = self._motivo_para_descartar(texto_comando)
        if motivo is not None:
            logger.info("acordou mas nao veio comando aproveitavel (%s) - abrindo a sessao", motivo)
        else:
            await self._event_bus.publish("voice.command_received", {"texto": texto_comando})
            await self._event_bus.publish("voice.transcription_ready", {"texto": texto_comando})
            await self._responder(texto_comando)

        await self._conversar_ate_o_silencio()
        await self._definir_estado(EstadoVoz.IDLE)
        return True

    async def _responder(self, texto_comando: str) -> None:
        """Pensa e fala. Extraido de `ciclo_uma_vez` para a sessao poder
        repetir isto sem repetir a deteccao da palavra de ativacao."""
        await self._definir_estado(EstadoVoz.THINKING)
        if self._processar_comando_em_frases is not None:
            await self._responder_falando_por_frases(texto_comando)
            return
        try:
            resposta = await self._processar_comando(texto_comando)
        except Exception:
            logger.exception("Falha ao processar comando de voz")
            resposta = "Desculpa, tive um problema para processar isso."

        await self._definir_estado(EstadoVoz.SPEAKING)
        await self._event_bus.publish("voice.response_started", {"texto": resposta})
        await self._falar_com_protecao(resposta)
        await self._event_bus.publish("voice.response_finished", {"texto": resposta})

    async def _conversar_ate_o_silencio(self) -> None:
        """A CONVERSA CONTINUA SEM PRECISAR CHAMAR DE NOVO (2026-08-02).

        Pedido do dono, depois de testar: *"ta horrivel"* - ele tinha que
        dizer "Fofao" antes de CADA frase.

        E o log mostrou que nao era o microfone. Numa das tentativas ele falou
        tres vezes seguidas e as tres foram transcritas LIMPAS:

            'Voce ja jogou bola?'
            'Voce ja jogou bola alguma vez?'
            'Voce gosta de jogar bola?'

        O robo ouviu perfeitamente as tres e DESCARTOU as tres, porque nenhuma
        tinha a palavra de ativacao. Ele repetiu e reformulou achando que nao
        tinha sido ouvido. Tinha.

        Aqui a palavra de ativacao passa a ABRIR uma sessao: enquanto a pessoa
        continuar falando, cada fala e um comando. O silencio e que encerra -
        e ai a palavra volta a ser exigida.

        Isto so e seguro por causa do assentamento pos-fala (ver
        `_falar_com_protecao`): sem ele, a sessao aberta faria o robo ouvir a
        propria resposta e conversar consigo mesmo em laco - que e exatamente
        o que aconteceu em 2026-08-02 antes daquele conserto.
        """
        silencios = 0
        while silencios < self._silencios_para_fechar_sessao:
            await self._definir_estado(EstadoVoz.LISTENING)
            try:
                audio = await self._gravar_audio(self._duracao_comando_s)
            except ErroAudio as erro:
                await self._publicar_erro_audio(erro)
                return

            if self._detector_atividade is not None and not self._detector_atividade.tem_som(
                audio
            ):
                silencios += 1
                continue

            texto = await self._transcritor.transcrever(audio)
            motivo = self._motivo_para_descartar(texto)
            if motivo == "alucinacao":
                # Nao conta como silencio NEM como fala: o Whisper entrou em
                # laco, e nao ha informacao nenhuma ali - nem para responder,
                # nem para concluir que a pessoa foi embora.
                logger.info("descartando transcricao em laco (alucinacao): %.60s...", texto)
                continue
            if motivo is not None:
                # Vazio ou eco contam como SILENCIO. No eco, o motivo e menos
                # obvio: se o que voltou foi a voz dele, entao ninguem falou
                # com ele nessa janela - e a sessao tem que poder fechar.
                # Tratar como fala mantinha a sessao aberta para sempre,
                # alimentando o proprio laco.
                if motivo == "eco":
                    logger.info("descartando eco da propria fala: %.60s...", texto)
                silencios += 1
                continue

            logger.info("sessao aberta - comando sem palavra de ativacao: %r", texto)
            await self._event_bus.publish("voice.command_received", {"texto": texto})
            await self._event_bus.publish("voice.transcription_ready", {"texto": texto})
            await self._responder(texto)
            silencios = 0

        logger.info("sessao encerrada por silencio - a palavra de ativacao volta a ser exigida")

    #: Quantas repeticoes da MESMA frase curta denunciam laco do Whisper.
    _REPETICOES_PARA_ALUCINACAO = 5
    #: Uma so "palavra" com a mesma letra repetida tantas vezes nao e
    #: portugues - e o modelo travado num token ("FFFFFF...", 220 vezes).
    _LETRAS_REPETIDAS_PARA_ALUCINACAO = 8

    @classmethod
    def _parece_alucinacao(cls, texto: str) -> bool:
        """Whisper em laco, capturado ao vivo em 2026-08-02:

            'O que e o que e o que e o que e ...'   (~60 vezes)

        Ele faz isso com audio de pouco conteudo, e o robo respondia aquilo
        como se fosse pergunta - gastando um turno da conversa. Numa sessao
        aberta seria pior: viraria realimentacao.

        A assinatura e um trecho curto repetido muitas vezes seguidas. Fala
        humana nao faz isso; quando faz (alguem repetindo por enfase), sao
        duas ou tres vezes, nao cinco.

        POR QUE ELE DEIXAVA PASSAR (medido em 2026-08-05: 27 lacos no log do
        dia, 6 barrados). A versao anterior ancorava no COMECO da frase
        (`palavras[:tamanho]`), entao so via o laco que comecasse na primeira
        palavra. Escapavam tres formas, todas reais no log:

          'Voce nao sabe o que e o que e...'  laco comeca na 4a palavra
          'O que e o que e QUE E que e...'    o trecho encolhe no meio
          'FFFFFF...' (220 letras)            uma palavra so, nem era testado

        Agora a ancora desliza: qualquer posicao de partida serve.
        """
        # MINUSCULAS e sem pontuacao: o Whisper alterna a caixa entre as
        # repeticoes ("O que e o que e...") e um unico "O" contra "o" quebrava
        # a comparacao - o laco passava batido justamente no caso real que
        # motivou este codigo.
        palavras = [p.strip(".,!?;:").lower() for p in texto.split()]
        palavras = [p for p in palavras if p]
        # ANTES do piso de tamanho: o token travado e UMA palavra, e morria no
        # `len(palavras) < 5` sem nunca ser examinado.
        if any(cls._letra_travada(p) for p in palavras):
            return True
        # O piso e o proprio numero de repeticoes, nao o dobro: "sim sim sim
        # sim sim sim" tem 6 palavras e ja e claramente um laco.
        if len(palavras) < cls._REPETICOES_PARA_ALUCINACAO:
            return False
        for tamanho in range(1, 6):  # trechos de 1 a 5 palavras
            for partida in range(len(palavras) - tamanho + 1):
                trecho = palavras[partida : partida + tamanho]
                repeticoes = 0
                for inicio in range(partida, len(palavras) - tamanho + 1, tamanho):
                    if palavras[inicio : inicio + tamanho] != trecho:
                        break
                    repeticoes += 1
                if repeticoes >= cls._REPETICOES_PARA_ALUCINACAO:
                    return True
        return False

    #: Quantas palavras a transcricao precisa ter para ser testada como eco.
    #: Abaixo disso a coincidencia nao diz nada: "sim", "ta bom" ou "o que"
    #: batem com quase qualquer fala anterior por acaso.
    _PALAVRAS_MINIMAS_PARA_ECO = 4
    #: Que fracao das palavras ouvidas precisa estar na ultima fala dele.
    #: 0,7 e alto de proposito - ver o comentario sobre o erro barato.
    _FRACAO_PARA_ECO = 0.7

    def _eh_eco_da_propria_fala(self, texto: str) -> bool:
        """A transcricao e a voz DELE voltando pelo microfone?

        A trava de tempo (`_falar_na_tela` agora espera o som acabar) e a
        defesa principal; esta aqui e o cinto de seguranca, para o caso de o
        som escapar por uma fresta - um atraso na serial, um buffer do I2S
        maior que o previsto, o alto-falante e o microfone na mesma placa.

        NAO COMPARA TEXTO IGUAL: o Whisper devolve a fala dele mastigada, e
        exigir igualdade nao pegaria nada. Medido no laco de 2026-09-19:

            ele disse  "Duas coisas acontecerem juntas nao significa que..."
            voltou     "dois coisas acontecerem juntas now significa que..."

        Entao a conta e de SOBREPOSICAO: que fracao do que foi ouvido ja
        estava no que ele acabou de dizer. No exemplo acima, 8 das 9 palavras.

        O ERRO BARATO E PARA QUE LADO. Achar que a pessoa e eco custa uma fala
        perdida - ela repete, e a janela conta como silencio, entao a sessao
        ainda pode fechar. Achar que o eco e pessoa custa o robo conversando
        sozinho para sempre. Por isso o limiar e alto, mas existe.
        """
        if not self._ultima_fala:
            return False
        ouvidas = self._palavras(texto)
        if len(ouvidas) < self._PALAVRAS_MINIMAS_PARA_ECO:
            return False
        ditas = set(self._palavras(self._ultima_fala))
        em_comum = sum(1 for p in ouvidas if p in ditas)
        return (em_comum / len(ouvidas)) >= self._FRACAO_PARA_ECO

    def _motivo_para_descartar(self, texto: str) -> str | None:
        """Por que esta transcricao NAO deve virar um comando - ou None.

        POR QUE EXISTE (2026-09-20). Os tres testes abaixo ja eram feitos, mas
        so dentro de `_conversar_ate_o_silencio`. O caminho da palavra de
        ativacao (`ciclo_uma_vez`) mandava o texto para a IA sem olhar, e uma
        transcricao VAZIA chegava ate o modelo. Colhido no log:

            16:46:30  ouvi (rms=0.1486): 'Oi javis!'   -> acordou, respondeu
            16:46:39  ouvi (rms=0.0238): ''            -> foi para a IA assim
            16:46:39  400 Requests ending with a model turn are not supported

        O Gemini descarta a mensagem de usuario vazia, sobra o historico
        terminando na fala DELE, e a API recusa o pedido inteiro. Ou seja: uma
        janela de silencio virava uma chamada gasta e um erro no log - com a
        cota gratuita pagando a conta.

        Devolver o MOTIVO, e nao um booleano, e o que deixa os dois caminhos
        compartilharem o portao sem compartilhar a consequencia: so a sessao
        contabiliza silencio, e cada motivo pesa diferente ali (ver quem
        chama). O portao decide o que e aproveitavel; quem chama decide o que
        fazer com o descarte.
        """
        if not texto or not texto.strip():
            return "vazio"
        if self._parece_alucinacao(texto):
            return "alucinacao"
        if self._eh_eco_da_propria_fala(texto):
            return "eco"
        return None

    @staticmethod
    def _palavras(texto: str) -> list[str]:
        """So as palavras, minusculas, sem pontuacao nenhuma colada.

        Mesmo padrao de `_comando_na_janela`, e nao o `.strip(\".,!?;:\")` do
        `_parece_alucinacao`: aquele nao tira ASPAS, e a fala do robo tem. No
        primeiro teste contra o log real, ele disse `de outra \"oracao\"?` e o
        eco passou batido porque `\"oracao\"` nao casava com `oracao` - cinco
        das seis palavras batiam e a sexta morreu num par de aspas.
        """
        import re

        return re.findall(r"[\wÀ-ÿ]+", texto.lower())

    @classmethod
    def _letra_travada(cls, palavra: str) -> bool:
        """Mesma letra repetida seguidas vezes dentro de uma palavra so."""
        seguidas = 1
        for anterior, atual in zip(palavra, palavra[1:], strict=False):
            seguidas = seguidas + 1 if atual == anterior else 1
            if seguidas >= cls._LETRAS_REPETIDAS_PARA_ALUCINACAO:
                return True
        return False

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
