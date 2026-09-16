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

    async def iniciar(self) -> None:
        try:
            self._serial = serial.Serial(self._porta, self._baud, timeout=0)
        except serial.SerialException as erro:
            logger.warning("orion_face inalcancavel em %s: %s", self._porta, erro)
            self._serial = None
            return
        for topico in TOPICOS_REPASSADOS:
            self._event_bus.subscribe(topico, self._fazer_repassador(topico))
        self._event_bus.subscribe(TOPICO_AUDIO, self._receber_audio)
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
        """
        inicio = asyncio.get_event_loop().time()
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

    async def encerrar(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None
