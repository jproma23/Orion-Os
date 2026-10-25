"""Ponte entre o Event Bus e o rosto ESP32-S3 (orion_face), por USB serial.

A placa e so um periferico de SAIDA (rosto + voz) - decisao do dono
(2026-09-12): a rede WiFi que o orion_face usava antes esbarrou num
bloqueio fora do nosso controle (roteador do provedor, sem acesso). O cabo
USB que ja liga fisicamente a placa ao Pi carrega os eventos no lugar -
ver orion_face/src/net.cpp: handle_line() consome exatamente este formato.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import serial

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("orion.display.ponte_serial")

#: eventos que o firmware do orion_face realmente reage (ver
#: orion_face/src/net.cpp: handle_line)
TOPICOS_REPASSADOS = (
    "voice.response_started",
    "voice.response_finished",
    "motion.pan_tilt",
)

#: Eventos do robo que viram REACAO do rosto (2026-09-27). O firmware entende
#: {"evento": "rosto", "tipo": ...} (orion_face/src/net.cpp); os tipos, e o
#: que o rosto faz com cada um, estao em orion_face/src/rosto_emo.h. Quem
#: decide a traducao e o `TradutorRosto`, logo abaixo.
TOPICOS_ROSTO = (
    "vision.person_detected",
    "vision.person_recognized",
    "vision.person_lost",
    "voice.status",
)

#: A visao publica a CADA quadro. O rosto so faz a festa de "pessoa nova"
#: depois de 6s sem ninguem - repassar todo quadro so entupiria a serial, que
#: e a mesma por onde passa a fala.
_INTERVALO_PESSOA_S = 1.0

#: Reconhecer alguem = rosto muito feliz, mas uma vez por pessoa neste
#: intervalo. Quem fica na frente do robo seria reconhecido a cada quadro, e
#: festa sem fim deixa de ser festa.
_INTERVALO_RECONHECIDA_S = 300.0

#: estados do VoiceCore (voice_core.EstadoVoz) em que o rosto faz cara de
#: "estou te ouvindo" - LISTENING fica de fora: e a escuta de fundo, esperando
#: o nome dele, e dura o dia inteiro.
_ESTADOS_OUVINDO = ("WAKE_DETECTED", "TRANSCRIBING")
_ESTADO_PENSANDO = "THINKING"


class TradutorRosto:
    """Traduz eventos do Event Bus em reacoes do rosto. Sem porta nem bus:
    recebe (topico, dados) e devolve as linhas a mandar - por isso da para
    testar sem hardware. `relogio` e injetavel pelo mesmo motivo."""

    def __init__(self, relogio=time.monotonic) -> None:
        self._relogio = relogio
        self._ultima_pessoa = float("-inf")
        self._reconhecidas: dict[object, float] = {}
        self._ouvindo = False
        self._pensando = False

    def traduzir(self, topico: str, dados: dict) -> list[dict]:
        if topico == "vision.person_detected":
            agora = self._relogio()
            if agora - self._ultima_pessoa < _INTERVALO_PESSOA_S:
                return []
            self._ultima_pessoa = agora
            return [{"tipo": "pessoa"}]

        if topico == "vision.person_recognized":
            agora = self._relogio()
            quem = dados.get("pessoa_id") or dados.get("nome")
            if agora - self._reconhecidas.get(quem, float("-inf")) < _INTERVALO_RECONHECIDA_S:
                return []
            self._reconhecidas[quem] = agora
            # "emocao" e nao "pessoa": a deteccao chega ANTES do reconhecimento,
            # e o firmware ja gastou a festa de pessoa nova quando o nome chega.
            return [{"tipo": "emocao", "nome": "muito_feliz", "valor": 2000}]

        if topico == "vision.person_lost":
            self._ultima_pessoa = float("-inf")
            return [{"tipo": "pessoa_sumiu"}]

        if topico == "voice.status":
            estado = str(dados.get("estado", ""))
            ouvindo = estado in _ESTADOS_OUVINDO
            pensando = estado == _ESTADO_PENSANDO
            saida = []
            if ouvindo != self._ouvindo:
                saida.append({"tipo": "ouvindo_inicio" if ouvindo else "ouvindo_fim"})
            if pensando != self._pensando:
                saida.append({"tipo": "pensando_inicio" if pensando else "pensando_fim"})
            self._ouvindo, self._pensando = ouvindo, pensando
            return saida

        return []


#: A fala tambem vem por aqui desde 2026-09-16 (ver orion_face/src/net.cpp:
#: serial_task). Nao entra em TOPICOS_REPASSADOS porque nao e uma linha JSON:
#: e binario, e tem tratamento proprio.
TOPICO_AUDIO = "voice.audio_pcm"

#: Enquadramento do audio - o MESMO nos dois sentidos do cabo. O 0xFE marca o
#: inicio e nunca aparece em JSON valido (que e ASCII), e por isso serve de
#: divisor entre "linha de texto" e "quadro de audio" na mesma porta. Quando um
#: 0xFE (ou o proprio escape) aparece dentro do audio, vai escapado - senao um
#: byte de fala que por acaso valha 0xFE seria lido como um quadro novo.
_MARCA_QUADRO = 0xFE
_ESCAPE = 0xFD
_XOR_ESCAPE = 0x20

#: O I2S do firmware esta fixo nisto (orion_face/src/audio.cpp).
TAXA_TELA_HZ = 16000
#: 512 bytes = 256 amostras = 16ms de fala por quadro.
_BYTES_POR_QUADRO = 512

#: Quanta fala vai ADIANTADA, enchendo a fila do firmware antes de o envio
#: cair no ritmo normal (2026-09-17).
#:
#: Sem isso o Pi mandava no ritmo EXATO do consumo, com a fila comecando
#: vazia - entao qualquer atraso do event loop (o Pi roda visao, LiDAR e IA ao
#: mesmo tempo) deixava o alto-falante sem nada para tocar, e a fala
#: engasgava. O dono ouviu e relatou: "chega a travar as vezes a fala".
#:
#: 0,3s cabe com folga na fila de 16KB (0,5s) do firmware, entao nada e
#: descartado la; e atrasa o inicio da fala em 0,3s no pior caso - preco
#: barato por uma frase que nao corta no meio.
_FOLGA_INICIAL_S = 0.3

#: O firmware captura em ESTEREO (o I2S esta em modo estereo), mas o microfone
#: da placa e UM so - os dois canais carregam o mesmo sinal, medido em
#: 2026-09-16 (picos identicos: 50/50 em silencio, 423/423 com fala). Ainda
#: assim desintercalamos e ficamos com o canal mais FORTE em vez de somar: se
#: um dia houver dois microfones de verdade, somar criaria filtro pente e
#: abafaria justamente a fala (foi a medicao que definiu isso em
#: ouvidos_esp32.py).
_CANAIS_CAPTURA = 2
_BYTES_POR_AMOSTRA = 2

#: Teto do buffer de captura: ~4s de audio. A placa empurra 64KB/s sem parar,
#: e quase sempre ninguem esta gravando - sem teto, o buffer cresceria para
#: sempre segurando som que ninguem vai ouvir.
_BRUTO_MAXIMO_BYTES = TAXA_TELA_HZ * _CANAIS_CAPTURA * _BYTES_POR_AMOSTRA * 4

#: Ganho FIXO da captura (ver `_converter` para por que nao e automatico).
#: Leva a fala para a faixa de 0,05 a 0,2 de rms, em que o Whisper trabalha
#: bem, e mantem intacta a razao entre fala e silencio - que e o que o VAD usa
#: para decidir se vale acordar o Whisper.
#:
#: ERA 3x, calibrado em 2026-09-17 com a fala medida em rms 0,035 (o dono
#: falando perto da tela), que 3x levava a ~0,10. SUBIU PARA 5x em 2026-09-19:
#: na distancia real de uso a fala chega mais fraca - medido rms 0,0134 e pico
#: 0,164, que 3x levava a so 0,040, ABAIXO da faixa. O Whisper rodou (o VAD
#: disparou certo) e devolveu string VAZIA duas vezes seguidas: audio fraco
#: demais para virar palavra.
#:
#: 5x e o maior fator que ainda nao estoura: o pico medido de 0,164 vira 0,82,
#: com folga ate 1,0. O criterio para mexer aqui esta em `_converter` - ganho
#: so resolve problema de NIVEL. Neste caso a relacao sinal/ruido estava boa
#: (silencio 0,0030 contra fala 0,0134, 4,5x), entao o problema era nivel.
_GANHO_FIXO = 5.0

#: estados do desempacotador de entrada (espelham net.cpp no firmware)
_ENTRADA_TEXTO = 0
_ENTRADA_TAMANHO = 1
_ENTRADA_AUDIO = 2


def _escapar(dados: bytes) -> bytearray:
    saida = bytearray()
    for b in dados:
        if b in (_MARCA_QUADRO, _ESCAPE):
            saida.append(_ESCAPE)
            saida.append(b ^ _XOR_ESCAPE)
        else:
            saida.append(b)
    return saida


def empacotar_audio(pcm: bytes) -> bytes:
    """Monta um quadro: 0xFE + tamanho (2 bytes) + PCM, com byte-stuffing."""
    tam = len(pcm)
    quadro = bytearray([_MARCA_QUADRO])
    quadro += _escapar(bytes([(tam >> 8) & 0xFF, tam & 0xFF]))
    quadro += _escapar(pcm)
    return bytes(quadro)


def resolver_porta(porta_sugerida: str = "/dev/ttyACM0") -> str:
    """Encontra a porta serial do orion_face (ESP32-S3).

    Tolera re-enumeracao do kernel (usb disconnect/reconnect mudando de
    ttyACM0 para ttyACM1).
    """
    import glob
    from pathlib import Path

    if Path(porta_sugerida).exists():
        return porta_sugerida

    por_id = glob.glob("/dev/serial/by-id/*Espressif*")
    if por_id and Path(por_id[0]).exists():
        return por_id[0]

    acms = sorted(glob.glob("/dev/ttyACM*"))
    if acms:
        return acms[0]

    return porta_sugerida


class PonteSerial:
    """Escreve cada evento repassado como uma linha JSON na porta serial do
    orion_face. Nunca levanta: porta ausente ou fechada so registra e segue
    (Cap 6 s.8 - modulo ausente nao aborta o sistema)."""

    def __init__(
        self,
        event_bus: EventBus,
        porta: str = "/dev/ttyACM0",
        baud: int = 115200,
    ) -> None:
        self._event_bus = event_bus
        self._porta = porta
        self._baud = baud
        self._serial: serial.Serial | None = None
        #: Audio do microfone da tela ja recebido, ainda intercalado e cru.
        #: `bytearray` e nao numpy: concatenar array numpy a cada bloco
        #: copiaria o buffer inteiro dezenas de vezes por segundo.
        self._bruto = bytearray()
        #: Acordado a cada bloco novo - quem espera audio dorme aqui em vez de
        #: ficar em polling com sleep.
        self._chegou = asyncio.Event()
        self._tarefa_leitura: asyncio.Task | None = None
        #: estado do desempacotador (espelha a maquina de estado do firmware)
        self._estado = _ENTRADA_TEXTO
        self._escapando = False
        self._tam_bytes = bytearray()
        self._falta = 0
        #: quadro em montagem - so vai para `_bruto` quando fecha inteiro
        self._quadro = bytearray()
        #: enquanto True, o buffer NAO e podado pelo teto (ver `_desempacotar`)
        self._gravando = False
        self._encerrando = False
        self._tradutor_rosto = TradutorRosto()

    @property
    def ouvindo(self) -> bool:
        """True quando ha porta aberta e a leitura do microfone esta de pe."""
        return self._serial is not None and self._tarefa_leitura is not None

    async def iniciar(self) -> None:
        self._porta = resolver_porta(self._porta)
        try:
            self._serial = serial.Serial(self._porta, self._baud, timeout=0)
        except serial.SerialException as erro:
            logger.warning("orion_face inalcancavel em %s: %s", self._porta, erro)
            self._serial = None
        for topico in TOPICOS_REPASSADOS:
            self._event_bus.subscribe(topico, self._fazer_repassador(topico))
        self._event_bus.subscribe(TOPICO_AUDIO, self._receber_audio)
        for topico in TOPICOS_ROSTO:
            self._event_bus.subscribe(topico, self._fazer_tradutor(topico))
        self._tarefa_leitura = asyncio.create_task(self._drenar_sempre())
        if self._serial is not None:
            logger.info("Ponte serial com orion_face pronta (%s)", self._porta)

    def _fazer_repassador(self, topico: str):
        async def _repassar(evento: Evento) -> None:
            if self._serial is None:
                return
            linha = json.dumps({"evento": topico, **evento.dados}) + "\n"
            try:
                self._serial.write(linha.encode("utf-8"))
            except serial.SerialException:
                logger.warning("Falha ao escrever no orion_face, ponte segue tentando")

        return _repassar

    def _fazer_tradutor(self, topico: str):
        async def _traduzir(evento: Evento) -> None:
            if self._serial is None:
                return
            for reacao in self._tradutor_rosto.traduzir(topico, evento.dados or {}):
                logger.info("rosto <- %s (de %s)", reacao, topico)  # DIAGNOSTICO TEMPORARIO
                linha = json.dumps({"evento": "rosto", **reacao}) + "\n"
                try:
                    self._serial.write(linha.encode("utf-8"))
                except serial.SerialException:
                    logger.warning("Falha ao escrever no orion_face, ponte segue tentando")
                    return

        return _traduzir

    async def _receber_audio(self, evento: Evento) -> None:
        """Assinante de `voice.audio_pcm`: solta o envio numa task e volta JA.

        `EventBus._despachar` e serializado: um assinante lento segura TODOS os
        outros eventos ate terminar. Enviar uma fala inteira leva o tempo da
        fala (segundos) - com `await` direto aqui, o rosto pararia de receber
        pan/tilt e ate o fim da propria fala enquanto ela toca.
        """
        audio = evento.dados.get("audio")
        taxa = evento.dados.get("taxa")
        if audio is None or not taxa or self._serial is None:
            return
        asyncio.create_task(self._enviar_audio(self._preparar(audio, taxa)))

    @staticmethod
    def _preparar(audio, taxa: int) -> bytes:
        """PCM na taxa nativa do Piper -> bytes na taxa que a tela toca.

        O I2S do firmware esta fixo em 16kHz MONO; tocar os 22050Hz do Piper
        sem converter sairia acelerado e agudo. O firmware duplica mono para
        estereo sozinho, entao aqui nao se mexe em canais.
        """
        # import local: quem nao tem a placa plugada nao paga numpy por isso
        from orion.voice.audio_utils import reamostrar

        if taxa != TAXA_TELA_HZ:
            audio = reamostrar(audio, taxa, TAXA_TELA_HZ)
        return audio.tobytes()

    async def _enviar_audio(self, pcm: bytes) -> None:
        """Escreve a fala na serial NO RITMO em que o alto-falante consome.

        Sem esse ritmo o Pi despeja tudo de uma vez, a fila de 16KB do firmware
        enche e o excedente e descartado la (com aviso no log do ESP) - a frase
        sai cortada no meio. O `sleep` tambem e o que mantem o event loop livre
        durante a fala.

        Os primeiros `_FOLGA_INICIAL_S` saem o mais rapido que a USB aceita,
        de proposito: e o colchao que segura um engasgo do event loop sem o
        alto-falante ficar sem nada para tocar.
        """
        inicio = asyncio.get_event_loop().time() - _FOLGA_INICIAL_S
        enviados = 0
        for i in range(0, len(pcm), _BYTES_POR_QUADRO):
            bloco = pcm[i:i + _BYTES_POR_QUADRO]
            if self._serial is None:  # encerrou no meio da fala
                return
            try:
                self._serial.write(empacotar_audio(bloco))
            except serial.SerialException:
                logger.warning("Falha ao enviar fala para o orion_face")
                return
            enviados += len(bloco)

            # 2 bytes por amostra: o instante em que este bloco acaba de tocar.
            alvo = inicio + (enviados / 2) / TAXA_TELA_HZ
            atraso = alvo - asyncio.get_event_loop().time()
            if atraso > 0:
                await asyncio.sleep(atraso)

    # -----------------------------------------------------------------------
    # O microfone da tela (2026-09-17)
    # -----------------------------------------------------------------------
    #
    # O mesmo cabo que leva a fala traz o microfone de volta: o firmware
    # (orion_face/src/audio.cpp, capture_task) empurra o audio capturado sem
    # parar, em quadros 0xFE identicos aos que recebe. Quem le e esta ponte,
    # e nao uma classe separada, por um motivo duro: a porta serial tem UMA
    # dona. Dois objetos abrindo /dev/ttyACM0 dividiriam os bytes entre si de
    # forma imprevisivel.
    #
    # A leitura e CONTINUA, e nao sob demanda, pelo mesmo motivo que os
    # ouvidos do ESP32 drenam o socket sempre (ver ouvidos_esp32.py): a placa
    # empurra 64KB/s e o buffer do sistema entregaria o som de varios segundos
    # atras - o robo responderia ao passado.

    async def _drenar_sempre(self) -> None:
        """Le a serial para sempre, reconectando se a porta cair."""
        while not self._encerrando:
            if self._serial is None:
                porta = resolver_porta(self._porta)
                try:
                    self._serial = serial.Serial(porta, self._baud, timeout=0)
                    self._porta = porta
                    logger.info("Ponte serial com orion_face reconectada (%s)", porta)
                except (serial.SerialException, OSError):
                    await asyncio.sleep(2.0)
                    continue

            try:
                dados = self._serial.read(4096)  # timeout=0: volta na hora
            except (serial.SerialException, OSError) as erro:
                logger.warning(
                    "Leitura da serial do orion_face falhou (%s), tentando reconectar: %s",
                    self._porta,
                    erro,
                )
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
                await asyncio.sleep(1.0)
                continue

            if dados:
                self._desempacotar(dados)
                await asyncio.sleep(0)  # cede o loop sem dormir de verdade
            else:
                await asyncio.sleep(0.01)

    def _desempacotar(self, dados: bytes) -> None:
        """Máquina de estado espelhada do firmware (net.cpp: serial_task).

        A mesma porta carrega o log do `ESP_LOG` (texto ASCII) e o audio
        (binario). O 0xFE separa os dois: ele nunca aparece em ASCII, e dentro
        do audio vem escapado.
        """
        # O quadro so vai para o buffer quando chega INTEIRO. Meio quadro pode
        # comecar no meio de uma amostra, e um unico byte fora de lugar
        # desalinha os canais e transforma a fala em ruido branco de escala
        # cheia - medido em 2026-09-17: rms=0,57 com pico=1,0 e o Whisper
        # transcrevendo vazio. Como `gravar()` esvazia o buffer a qualquer
        # instante, e so montando o quadro de lado que a fronteira fica
        # garantida.
        chegou_algo = False
        for b in dados:
            if self._estado == _ENTRADA_TEXTO:
                if b == _MARCA_QUADRO:
                    self._estado = _ENTRADA_TAMANHO
                    self._tam_bytes.clear()
                    self._quadro.clear()
                    self._escapando = False
                continue  # fora de quadro e log do ESP - nao interessa aqui

            if self._escapando:
                valor = b ^ _XOR_ESCAPE
                self._escapando = False
            elif b == _ESCAPE:
                self._escapando = True
                continue
            else:
                valor = b

            if self._estado == _ENTRADA_TAMANHO:
                self._tam_bytes.append(valor)
                if len(self._tam_bytes) == 2:
                    self._falta = (self._tam_bytes[0] << 8) | self._tam_bytes[1]
                    # tamanho absurdo = cabecalho corrompido (ou um 0xFE que
                    # vazou do log): volta ao texto em vez de engolir lixo
                    self._estado = (
                        _ENTRADA_AUDIO if 0 < self._falta <= 8192 else _ENTRADA_TEXTO
                    )
                continue

            self._quadro.append(valor)
            self._falta -= 1
            if self._falta == 0:
                # quadro completo: so agora ele conta como audio de verdade
                if len(self._quadro) % (_CANAIS_CAPTURA * _BYTES_POR_AMOSTRA) == 0:
                    self._bruto.extend(self._quadro)
                    chegou_algo = True
                else:
                    # firmware manda blocos alinhados; se chegou torto, foi
                    # corrupcao no cabo - descartar e melhor que desalinhar
                    # todo o resto da captura
                    logger.warning(
                        "quadro de audio torto (%d bytes) - descartado",
                        len(self._quadro),
                    )
                self._quadro.clear()
                self._estado = _ENTRADA_TEXTO

        if chegou_algo:
            # O teto so vale com NINGUEM gravando. Aparar durante uma gravacao
            # e uma corrida contra ela mesma: pedindo 6s de audio com teto de
            # 4s, o buffer era podado antes de completar e `gravar` esperava
            # para sempre - "o microfone parou de entregar audio (256000 de
            # 384000 bytes)", medido em 2026-09-17. Fora da gravacao o teto
            # continua necessario: a placa empurra 64KB/s sem parar e quase
            # sempre ninguem esta ouvindo.
            excesso = 0 if self._gravando else len(self._bruto) - _BRUTO_MAXIMO_BYTES
            if excesso > 0:
                # arredonda para quadro de amostra inteiro: cortar no meio de
                # uma amostra desalinharia os canais - o mesmo defeito que o
                # cuidado acima existe para evitar
                largura = _CANAIS_CAPTURA * _BYTES_POR_AMOSTRA
                del self._bruto[: excesso + (-excesso % largura)]
            self._chegou.set()

    async def gravar(self, duracao_s: float) -> "np.ndarray":  # noqa: F821
        """Devolve `duracao_s` de audio mono a 16kHz, pronto para o Whisper.

        MESMA assinatura de `captura_audio.gravar_trecho` e de
        `OuvidosESP32.gravar`, de proposito: entra no `VoiceCore` sem que o
        pipeline de voz precise saber que a fonte mudou de lugar.

        Semantica igual a do `sd.rec`: joga fora o acumulado e coleta o que
        chega A PARTIR DE AGORA - devolver "os ultimos N segundos" faria o
        VoiceCore transcrever duas vezes o mesmo trecho.
        """
        from orion.voice.captura_audio import ErroAudio

        if not self.ouvindo:
            raise ErroAudio(f"microfone da tela indisponivel ({self._porta})")

        self._bruto.clear()
        necessario = int(duracao_s * TAXA_TELA_HZ) * _CANAIS_CAPTURA * _BYTES_POR_AMOSTRA
        # folga generosa: a placa entrega em tempo real, entao esperar bem mais
        # que a duracao pedida significa que ela parou de mandar
        limite_s = duracao_s * 2 + 2.0
        self._gravando = True
        try:
            async with asyncio.timeout(limite_s):
                while len(self._bruto) < necessario:
                    self._chegou.clear()
                    await self._chegou.wait()
        except TimeoutError as erro:
            raise ErroAudio(
                f"o microfone da tela parou de entregar audio "
                f"({len(self._bruto)} de {necessario} bytes em {limite_s:.1f}s)"
            ) from erro
        finally:
            self._gravando = False

        return self._converter(bytes(self._bruto[:necessario]))

    @staticmethod
    def _converter(cru: bytes) -> "np.ndarray":  # noqa: F821
        """Bytes intercalados -> mono float32 em -1..1, com o canal mais forte."""
        import numpy as np

        from orion.voice.captura_audio import ErroAudio

        # descarta amostra sem par: um bloco pode cortar o quadro no meio, e
        # desalinhar canal seria pior do que perder uma amostra
        util = len(cru) - (len(cru) % (_CANAIS_CAPTURA * _BYTES_POR_AMOSTRA))
        amostras = np.frombuffer(cru[:util], dtype="<i2").astype(np.float32) / 32768.0
        if amostras.size == 0:
            raise ErroAudio("o microfone da tela entregou um trecho vazio")
        canais = [amostras[c::_CANAIS_CAPTURA] for c in range(_CANAIS_CAPTURA)]
        mono = max(canais, key=lambda c: float(np.abs(c).max()))

        # GANHO DIGITAL (2026-09-17). O microfone desta placa entrega fraco
        # mesmo com o ganho do codec no maximo (42dB): medido ao vivo,
        # rms=0,015 a 0,026 com o dono falando, onde fala saudavel fica em
        # 0,05-0,2. O Whisper erra nesse nivel - "Fofao" virou 'Fulfo!' e o
        # portao anti-invencao descartou, entao o robo ouvia e nao reagia.
        #
        # O GANHO E FIXO, e isso importa. Duas versoes automaticas falharam:
        #
        # 1. Normalizar pelo PICO: em trecho sem fala o pico tambem e baixo, o
        #    ganho ia ao teto e o ruido virava rms=0,58 de chiado - o Whisper
        #    passou a transcrever VAZIO.
        # 2. Normalizar pelo RMS: pior, e de um jeito traicoeiro. Ganho
        #    automatico da MAIS ganho ao silencio (8x) do que a fala (3x),
        #    entao ele ACHATA a diferenca entre os dois - os 7,7x medidos
        #    entre fala e ruido viravam ~2x. O VAD, que so chama o Whisper com
        #    som acima do ruido, deixou de ver fala: o robo ficou surdo
        #    justamente por causa do ajuste feito para ele ouvir melhor
        #    (medido: 1 unica transcricao em 6 minutos no ar).
        #
        # Um fator fixo levanta o nivel para o Whisper sem tocar na dinamica:
        # a razao entre fala e silencio sai igual a que entrou, que e
        # exatamente do que o VAD precisa.
        rms = float(np.sqrt(np.mean(np.square(mono))))
        pico = float(np.abs(mono).max())
        if _GANHO_FIXO != 1.0:
            # clip por seguranca: picos ocasionais de 0,8 ja foram medidos, e
            # estourar distorce - o clip so apara o excesso raro
            mono = np.clip(mono * _GANHO_FIXO, -1.0, 1.0)
        # INFO e nao debug: e com estes numeros que se decide se o problema e
        # nivel (ganho resolve) ou relacao sinal/ruido (ganho nao resolve, e
        # amplificar so piora).
        logger.info(
            "mic da tela: rms=%.4f pico=%.4f ganho=%.1fx (fixo)",
            rms, pico, _GANHO_FIXO,
        )
        return mono

    async def encerrar(self) -> None:
        self._encerrando = True
        tarefa, self._tarefa_leitura = self._tarefa_leitura, None
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        if tarefa is not None:
            tarefa.cancel()
            try:
                await tarefa
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass  # encerrar nunca levanta: estamos desligando
