"""Ouvidos do Jarvis: os dois microfones I2S do ESP32, por WiFi (Cap 9).

O ESP32 lê dois mics I2S a 16 kHz e despeja `int16` little-endian
INTERCALADO (esquerdo, direito, esquerdo, ...) num socket TCP. Este módulo é
o cliente desse socket, e entrega o áudio com a MESMA assinatura de
`captura_audio.gravar_trecho` - por isso entra no `VoiceCore` sem que o
pipeline de voz precise saber que a fonte mudou de lugar.

POR QUE ELE EXISTE (2026-07-29): o ESP32 transmitia corretamente - medido,
768000 bytes em exatos 12,0 s, taxa perfeita - e **nenhuma linha do orion-os
lia esse socket**. `grep` por "esp32", "9100" e "ouvidos" no projeto inteiro
dava zero. O robô ouvia `microfones_candidatos_indices`, dispositivos locais
do Notebook, enquanto o dono falava no ESP32. Ele estava surdo por dois
caminhos ao mesmo tempo: a fonte certa sem leitor, e o leitor na fonte errada.

O PONTO DE ENGENHARIA QUE DEFINE O MÓDULO: o ESP32 empurra áudio sem parar,
62,8 KB/s. Se só lêssemos o socket quando alguém pede áudio, o buffer do TCP
entregaria o som de VÁRIOS SEGUNDOS ATRÁS - o robô responderia ao passado. É
o mesmo problema que a câmera já teve nesta montagem ("descartando quadro
atrasado", `vision/captura.py`). Por isso existe uma tarefa que DRENA o socket
continuamente; quem pede áudio recebe o que está chegando agora.

Duas medições que decidiram detalhes daqui:
- **Não somar os dois canais.** Dois microfones separados no espaço, somados,
  produzem filtro pente e abafam justamente a fala. Usa-se o canal mais forte.
- **O IP muda.** Em 2026-07-29 o `10.20.20.101` anotado no journal já era o
  celular de alguém, e o ESP32 estava em `.104`. Por isso o host é resolvido
  por nome (mDNS, `ouvidos.local`) a cada conexão, e não guardado.
"""
from __future__ import annotations

import asyncio
import logging

import numpy as np

from orion.voice.captura_audio import ErroAudio

logger = logging.getLogger("orion.voice.ouvidos_esp32")

#: O firmware do ESP32 lê os dois mics a 16 kHz - a mesma taxa que o Whisper
#: espera, o que é sorte e não coincidência (foi por isso que este ESP32 foi
#: escolhido para o papel).
TAXA_ESP32 = 16000
#: esquerdo e direito intercalados, `int16` LE.
CANAIS_ESP32 = 2
BYTES_POR_AMOSTRA = 2

#: Silêncio máximo tolerado no socket antes de considerá-lo morto, em
#: segundos. O ESP32 empurra 62,8 KB/s sem parar - a 8192 bytes por leitura,
#: são ~8 leituras por segundo. Cinco segundos sem UM byte não é rede lenta,
#: é aparelho que sumiu. Não pode ser muito curto: derrubar a conexão por um
#: engasgo de WiFi custaria uma janela de escuta a cada engasgo.
SILENCIO_MAXIMO_S = 5.0


class OuvidosESP32:
    """Lê o áudio dos dois mics I2S do ESP32 e serve trechos sob demanda.

    Use como fonte do `VoiceCore`::

        ouvidos = OuvidosESP32("ouvidos.local", 9100)
        await ouvidos.iniciar()
        VoiceCore(..., gravar_audio=ouvidos.gravar)
    """

    def __init__(
        self,
        host: str = "ouvidos.local",
        porta: int = 9100,
        taxa: int = TAXA_ESP32,
        canais: int = CANAIS_ESP32,
        intervalo_reconexao_s: float = 3.0,
        timeout_conexao_s: float = 5.0,
        silencio_maximo_s: float = SILENCIO_MAXIMO_S,
    ) -> None:
        self._host = host
        self._porta = porta
        self._taxa = taxa
        self._canais = canais
        self._intervalo_reconexao_s = intervalo_reconexao_s
        self._timeout_conexao_s = timeout_conexao_s
        self._silencio_maximo_s = silencio_maximo_s
        self._executando = False
        self._conectado = False
        self._tarefa: asyncio.Task | None = None
        #: Amostras JÁ desintercaladas por canal, as mais recentes no fim.
        #: `bytearray` cru em vez de numpy: concatenar array numpy a cada
        #: pacote copiaria o buffer inteiro 8 vezes por segundo.
        self._bruto = bytearray()
        #: Acordado a cada pacote novo - quem espera áudio dorme aqui em vez
        #: de fazer polling com sleep.
        self._chegou = asyncio.Event()

    @property
    def conectado(self) -> bool:
        return self._conectado

    async def iniciar(self) -> None:
        """Sobe a tarefa que drena o socket. Não levanta se o ESP32 estiver
        ausente: ele pode aparecer depois (Cap 6 s.8 - módulo ausente não
        derruba o sistema), e quem chamar `gravar` nesse meio-tempo recebe
        `ErroAudio`, exatamente como um microfone local que falhou."""
        if self._executando:
            return
        self._executando = True
        self._tarefa = asyncio.create_task(self._drenar_sempre())

    async def encerrar(self) -> None:
        self._executando = False
        if self._tarefa is not None:
            self._tarefa.cancel()
            try:
                await self._tarefa
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass  # encerrar nunca levanta: estamos desligando
            self._tarefa = None
        self._conectado = False

    async def _drenar_sempre(self) -> None:
        """Reconecta para sempre. O ESP32 reinicia, o WiFi cai, o IP muda -
        nenhuma dessas coisas pode deixar o robô surdo de forma permanente."""
        while self._executando:
            try:
                await self._drenar_uma_conexao()
            except asyncio.CancelledError:
                raise
            except Exception:
                # WARNING e não debug de propósito: foi um `logger.debug` que
                # escondeu a falha do SET_PAN_TILT nesta mesma montagem, e o
                # sintoma foi "não funciona, e o log está limpo".
                logger.warning(
                    "ouvidos (%s:%d) caíram - tentando de novo em %.1fs",
                    self._host, self._porta, self._intervalo_reconexao_s,
                    exc_info=True,
                )
            self._conectado = False
            if self._executando:
                await asyncio.sleep(self._intervalo_reconexao_s)

    async def _drenar_uma_conexao(self) -> None:
        # O host é resolvido AQUI, a cada tentativa, e não guardado: em
        # 2026-07-29 o IP anotado no journal já pertencia a outro aparelho.
        leitor, escritor = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._porta),
            timeout=self._timeout_conexao_s,
        )
        self._conectado = True
        logger.info("ouvidos do ESP32 conectados em %s:%d", self._host, self._porta)
        try:
            while self._executando:
                # O `wait_for` é o coração desta função, e custou 3h35 de
                # surdez em 2026-08-14 para existir. `read()` sozinho SÓ
                # levanta quando o outro lado fecha o socket direito. Quando o
                # ESP32 some sem fechar - queda de energia, WiFi que evapora,
                # o que aconteceu às 17:24 daquele dia - o socket fica ESTAB
                # para sempre, esta linha dorme para sempre, e o laço de
                # reconexão logo acima nunca é acionado porque NADA levanta.
                # O robô ficou surdo com `_conectado = True`, a tela dizendo
                # "ouvindo", e nenhuma linha no log. A câmera e o link do
                # Motion Core reconectaram sozinhos na mesma queda: os dois
                # tinham timeout de leitura, este não tinha.
                try:
                    pedaco = await asyncio.wait_for(
                        leitor.read(8192), timeout=self._silencio_maximo_s
                    )
                except TimeoutError as erro:
                    raise ConnectionError(
                        f"ESP32 mudo ha {self._silencio_maximo_s:.1f}s "
                        f"(socket aberto, sem dados) - tratando como queda"
                    ) from erro
                if not pedaco:
                    raise ConnectionError("ESP32 fechou a conexão")
                self._bruto.extend(pedaco)
                self._limitar_buffer()
                self._chegou.set()
        finally:
            escritor.close()
            try:
                await escritor.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    def _limitar_buffer(self) -> None:
        """Teto do buffer. Sem ele, o áudio acumularia para sempre enquanto
        ninguém pedisse - vazamento de memória num processo que já roda com
        2,6 GB e tem um guardião de RAM justamente por isso."""
        teto = self._bytes_para(_SEGUNDOS_MAXIMOS_BUFFER)
        if len(self._bruto) > teto:
            del self._bruto[: len(self._bruto) - teto]

    def _bytes_para(self, segundos: float) -> int:
        return int(segundos * self._taxa) * self._canais * BYTES_POR_AMOSTRA

    async def gravar(self, duracao_s: float) -> np.ndarray:
        """Devolve `duracao_s` de áudio mono a 16 kHz, pronto para o Whisper.

        Semântica IGUAL à do `sd.rec` do microfone local, de propósito: joga
        fora o que estava acumulado e coleta o que chega A PARTIR DE AGORA.
        Devolver "os últimos N segundos" seria tentador, mas faria o
        `VoiceCore` transcrever duas vezes o mesmo trecho entre chamadas
        consecutivas.
        """
        if not self._conectado:
            raise ErroAudio(f"ouvidos do ESP32 indisponíveis ({self._host}:{self._porta})")

        self._bruto.clear()  # o passado não interessa: queremos o agora
        necessario = self._bytes_para(duracao_s)
        # Folga generosa: o ESP32 entrega em tempo real, então esperar muito
        # mais que a duração pedida significa que ele parou de falar.
        limite_s = duracao_s * 2 + 2.0
        try:
            async with asyncio.timeout(limite_s):
                while len(self._bruto) < necessario:
                    self._chegou.clear()
                    await self._chegou.wait()
        except TimeoutError as erro:
            raise ErroAudio(
                f"ouvidos do ESP32 pararam de entregar áudio "
                f"({len(self._bruto)} de {necessario} bytes em {limite_s:.1f}s)"
            ) from erro

        janela = bytes(self._bruto[:necessario])
        # Tira do buffer SO o que foi entregue: o que chegou depois fica, para
        # `continuar` poder pegar a fala que veio logo em seguida.
        del self._bruto[:necessario]
        return self._converter(janela)

    async def continuar(self, duracao_s: float) -> np.ndarray:
        """Como `gravar`, mas SEM jogar fora o que chegou desde a ultima janela.

        POR QUE (2026-11-08): "Jarvis, me siga" numa respirada so. A janela de
        ativacao pegou "Jarvis" no fim; o "me siga" chegou enquanto ele
        transcrevia (~2 s) e respondia "Oi? Pode falar!" - e o `gravar` seguinte
        comecava com `clear()`, jogando fora justamente a fala do pedido. Aqui o
        que ja esta no buffer conta como o comeco da janela.
        """
        if not self._conectado:
            raise ErroAudio(f"ouvidos do ESP32 indisponíveis ({self._host}:{self._porta})")
        necessario = self._bytes_para(duracao_s)
        limite_s = duracao_s * 2 + 2.0
        try:
            async with asyncio.timeout(limite_s):
                while len(self._bruto) < necessario:
                    self._chegou.clear()
                    await self._chegou.wait()
        except TimeoutError as erro:
            raise ErroAudio(
                f"ouvidos do ESP32 pararam de entregar áudio "
                f"({len(self._bruto)} de {necessario} bytes em {limite_s:.1f}s)"
            ) from erro
        janela = bytes(self._bruto[:necessario])
        del self._bruto[:necessario]
        return self._converter(janela)

    def _converter(self, cru: bytes) -> np.ndarray:
        """Bytes intercalados -> mono float32, com o canal MAIS FORTE.

        Não soma os canais: dois microfones separados no espaço, somados,
        criam filtro pente e abafam a fala (é o motivo pelo qual a soma foi
        descartada na medição de 2026-07-28). O canal mais forte é o que está
        virado para quem fala, e é o melhor material para o Whisper.
        """
        # descarta byte ímpar e amostra sem par - um pacote pode cortar o
        # quadro no meio, e desalinhar canal seria pior que perder 1 amostra
        util = len(cru) - (len(cru) % (self._canais * BYTES_POR_AMOSTRA))
        amostras = np.frombuffer(cru[:util], dtype="<i2").astype(np.float32) / 32768.0
        if amostras.size == 0:
            raise ErroAudio("ouvidos do ESP32 entregaram um trecho vazio")

        canais = [amostras[c :: self._canais] for c in range(self._canais)]
        # Remove o DC antes de comparar: um canal com offset constante parece
        # "mais forte" sem ter mais som nenhum. Foi assim que o microfone da
        # webcam enganou a seleção por qualidade em 2026-07-18.
        canais = [c - np.float32(c.mean()) for c in canais]
        forte = max(canais, key=lambda c: float(np.sqrt(np.mean(c**2))))
        return np.ascontiguousarray(forte)


#: Teto do buffer de drenagem. 30 s cobre com folga a maior janela que o
#: `VoiceCore` pede (comando de 5 s) mesmo se ele demorar para voltar a
#: pedir, e custa ~1,9 MB - barato perto do custo de vazar memória.
_SEGUNDOS_MAXIMOS_BUFFER = 30.0
