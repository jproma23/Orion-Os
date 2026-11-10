"""Sintese de voz com Piper (Cap 9 secao 3, passos 8-9).

Cancelamento de eco simples (Cap 9 s.7): quem orquestra o Voice Core deve
parar de escutar/transcrever enquanto `falar()` esta rodando (o estado
SPEAKING cobre isso) - este modulo so cuida de sintetizar e reproduzir.

`indice_dispositivo_saida`: nesta montagem o alto-falante interno e o P2
ainda nao aparecem no audio do Linux (falta driver/quirk do codec HDA) -
o padrao usa o dispositivo de audio USB confirmado funcionando
(config/orion.yaml: voice.saida_audio_indice). None deixa o
sounddevice escolher o dispositivo padrao do sistema.
"""
from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import wave

import numpy as np
import sounddevice as sd
from piper import PiperVoice

from orion.voice import timbre as _timbre
from orion.voice.audio_utils import reamostrar as _reamostrar

logger = logging.getLogger("orion.voice.sintese")

#: Serializa o `sd.play()` da thread de fala com o `sd.stop()` do
#: cancelamento (2026-11-10, achado C6-03). O stream do sounddevice e GLOBAL
#: no processo, entao a trava tambem e global (e nao por instancia). Sem ela,
#: um `sd.stop()` chamado do laco asyncio no meio de um `sd.play()` ainda
#: abrindo o stream e a mesma corrida nativa que dava o SEGV de 05/08.
_trava_sd = threading.Lock()


def _recarregar_portaudio() -> None:
    """Refaz a lista de dispositivos do PortAudio.

    O PortAudio lista as placas de som UMA vez, quando o `sounddevice` e
    importado. No boot do Notebook o alto-falante USB aparece alguns segundos
    DEPOIS do processo subir; sem refazer a lista ele nunca e encontrado e a
    primeira fala levantava ValueError, derrubando conversa, camera e chat
    juntos (01/11 e 08/11). E o caminho que o proprio sounddevice indica para
    reler os dispositivos. Seguro aqui porque o unico stream do processo e o
    `sd.play` desta classe, que roda sob `_trava_fala`.
    """
    sd._terminate()
    sd._initialize()

#: A VOZ DO FOFAO (2026-08-03) - pedido do dono: "caloroso e presente".
#:
#: O modelo continua o `pt_BR-faber-medium`, que e a voz padrao de portugues do
#: Brasil no Piper. O que muda o carater nao e o modelo, sao estes tres numeros
#: mais o tratamento em `orion.voice.timbre`.
#:
#: `length_scale` levemente acima de 1 deixa a fala menos apressada - quem fala
#: com calma soa mais acolhedor. Nao muito acima: pausado demais vira solene, e
#: solene e frio, o oposto do pedido.
#:
#: `noise_scale` um pouco acima do padrao (0,667) devolve variacao a entoacao.
#: Voz sem variacao soa robotica; variacao demais soa insegura.
#:
#: Nada disso custa latencia da PRIMEIRA frase: o streaming por frases ja
#: garante que o robo comeca a falar antes de terminar de gerar.
#: 1.05 -> 1.18 em 2026-09-17, a pedido do dono ouvindo o robo falar pela tela:
#: *"ele esta falando rapido demais, precisamos que ele fale mais lento"*. O
#: limite descrito abaixo continua valendo - pausado demais vira solene, e
#: solene e frio -, entao subiu com margem, nao ate onde daria.
#: 1.18 -> 1.40 em 2026-09-27, o dono de novo, ouvindo pela tela com o volume
#: mais alto: *"ta falando tao rapido que embola"*. Conferido antes que nao
#: era a tela pulando pedacos: frase de 5,4s tocada inteira, zero descarte no
#: log do firmware. Era ritmo mesmo.
VOZ_LENGTH_SCALE = 1.40
VOZ_NOISE_SCALE = 0.70
VOZ_NOISE_W_SCALE = 0.80


class Sintetizador:
    def __init__(
        self,
        caminho_modelo: str,
        indice_dispositivo_saida: int | None = None,
        length_scale: float = VOZ_LENGTH_SCALE,
        noise_scale: float = VOZ_NOISE_SCALE,
        noise_w_scale: float = VOZ_NOISE_W_SCALE,
        aplicar_timbre: bool = True,
    ) -> None:
        #: UMA FALA DE CADA VEZ - e isto e o que impede o robo de MORRER.
        #:
        #: Diagnosticado em 2026-08-05, depois de 10 quedas em 7 dias que
        #: ninguem tinha explicado. O servico nao levantava excecao: era morto
        #: pelo sistema com SIGSEGV (`code=killed, status=11/SEGV`), e o
        #: systemd o reerguia ~45 s depois. Do lado de fora parecia
        #: "o Fofao desconectou sozinho".
        #:
        #: Nas 4 quedas examinadas a ultima linha do log e sempre a mesma
        #: coisa: DUAS falas encostadas, uma saudacao espontanea junto de uma
        #: resposta da conversa. E ha dois caminhos independentes ate aqui -
        #: `voice_core.falar` (a conversa, disparada em `create_task`, sem
        #: ninguem esperando) e `sintetizador.falar` direto (a saudacao e a
        #: entrega de recado). Quando os dois se cruzam, `falar` roda duas
        #: vezes ao mesmo tempo, e o `asyncio.to_thread` poe as duas em
        #: THREADS de verdade em cima de dois recursos nativos que nao
        #: aguentam isso:
        #:
        #:   - `PiperVoice.synthesize_wav` - objeto C++/ONNX unico e
        #:     compartilhado, sem reentrancia;
        #:   - `sd.play()`/`sd.wait()` - o sounddevice guarda UM stream
        #:     global. A segunda `play` derruba o stream enquanto a primeira
        #:     thread ainda esta dentro do `wait`, e a leitura do que ja foi
        #:     liberado e o segfault.
        #:
        #: A trava serializa: a segunda fala espera a primeira terminar.
        #: Ela ATRASA, nunca descarta - era exatamente a ressalva escrita em
        #: `voice_core.falar` ("uma fila mal feita esconderia a resposta em
        #: vez de atrasa-la"). As duas continuam sendo ditas, e um robo que
        #: fala 2 s depois e melhor que um robo que morre no meio da frase -
        #: foi assim que o primeiro recado entregue da historia do projeto se
        #: perdeu, com a pessoa na frente ouvindo.
        self._trava_fala = asyncio.Lock()
        #: QUANDO ele falou, para a escuta descartar o proprio eco por TEMPO
        #: (2026-11-01). `falando` cobre a fala em curso; `fim_ultima_fala` e
        #: o instante (time.monotonic) em que a ultima terminou. Mora aqui
        #: porque TODA fala passa por `falar` - resposta, saudacao, recado.
        self.falando = False
        self.fim_ultima_fala = float("-inf")
        self._voz = PiperVoice.load(caminho_modelo)
        self._indice_dispositivo_saida = indice_dispositivo_saida
        self._aplicar_timbre = aplicar_timbre
        # Import tardio e tolerante: versoes antigas do piper nao tem
        # SynthesisConfig. Sem ele a voz sai no padrao, que e o que acontecia
        # ate hoje - nunca deixa de falar por causa de um ajuste de timbre.
        try:
            from piper.config import SynthesisConfig

            self._config = SynthesisConfig(
                length_scale=length_scale,
                noise_scale=noise_scale,
                noise_w_scale=noise_w_scale,
            )
        except Exception:
            self._config = None

    def _sintetizar(self, texto: str) -> tuple[np.ndarray, int]:
        """Texto -> PCM 16-bit na taxa nativa do Piper, ja com o timbre."""
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_out:
            if self._config is not None:
                self._voz.synthesize_wav(texto, wav_out, syn_config=self._config)
            else:
                self._voz.synthesize_wav(texto, wav_out)
        buffer.seek(0)
        with wave.open(buffer, "rb") as wav_in:
            dados = wav_in.readframes(wav_in.getnframes())
            taxa = wav_in.getframerate()
        audio = np.frombuffer(dados, dtype=np.int16)

        # O TIMBRE ANTES DA REAMOSTRAGEM, de proposito: a equalizacao e
        # feita em cima da taxa NATIVA do Piper, onde o espectro esta
        # intacto. Reamostrar primeiro e equalizar depois trabalharia sobre
        # um sinal que ja passou por interpolacao.
        if self._aplicar_timbre:
            audio = _timbre.aquecer(audio, taxa)
        return audio, taxa

    async def falar(self, texto: str) -> None:
        #: Pedido de cancelamento vindo do laco asyncio (2026-11-10, C6-03).
        #: A thread olha antes de tocar; se ja estiver tocando, o `sd.stop()`
        #: do cancelamento interrompe o `sd.wait()`.
        cancelada = threading.Event()

        def _sintetizar_e_reproduzir() -> None:
            audio, taxa = self._sintetizar(texto)

            # o dispositivo de saida pode nao suportar a taxa nativa do
            # Piper (ex.: USB Audio Device so aceita 44100Hz) - reamostra
            # em vez de deixar o PortAudio recusar com "Invalid sample rate".
            try:
                info = sd.query_devices(self._indice_dispositivo_saida)
            except ValueError:
                _recarregar_portaudio()
                try:
                    info = sd.query_devices(self._indice_dispositivo_saida)
                except ValueError:
                    # Sem alto-falante ele fica mudo nesta frase, mas o resto
                    # do robo continua de pe. A proxima fala tenta de novo.
                    logger.warning(
                        "saida de audio %r nao encontrada - frase nao falada: %r",
                        self._indice_dispositivo_saida,
                        texto[:60],
                    )
                    return
            taxa_dispositivo = int(info["default_samplerate"])
            if taxa_dispositivo != taxa:
                audio = _reamostrar(audio, taxa, taxa_dispositivo)
                taxa = taxa_dispositivo

            with _trava_sd:
                if cancelada.is_set():
                    return  # cancelaram durante a sintese: nem comeca a tocar
                sd.play(audio, samplerate=taxa, device=self._indice_dispositivo_saida)
            sd.wait()

        # A trava cobre a sintese E a reproducao: soltar depois do
        # `synthesize_wav` deixaria dois `sd.play()` se cruzarem, que e a
        # metade do problema que mata o processo. Ver `_trava_fala`.
        async with self._trava_fala:
            self.falando = True
            tarefa = asyncio.ensure_future(asyncio.to_thread(_sintetizar_e_reproduzir))
            try:
                # shield: cancelar `falar` NAO cancela a espera da thread.
                # Antes (ate 2026-11-10, C6-03) o cancelamento (Professor.parar,
                # o "para" falado) saia do `async with` na hora e soltava a
                # trava com a thread AINDA tocando - a fala seguinte entrava
                # num segundo `sd.play()` simultaneo, o SEGV de 05/08 - e
                # `falando` virava False com o audio no ar (a escuta religava
                # e ouvia a propria voz).
                await asyncio.shield(tarefa)
            except asyncio.CancelledError:
                cancelada.set()
                with _trava_sd:
                    try:
                        sd.stop()
                    except Exception:
                        logger.exception("sd.stop() falhou ao cancelar a fala")
                # So solta a trava (e o `falando`) depois que a thread acabou
                # de verdade. Outro cancel nesse meio-tempo nao encurta isso.
                # (`asyncio.wait` nao cancela nem relanca o erro da tarefa.)
                while not tarefa.done():
                    try:
                        await asyncio.wait({tarefa})
                    except asyncio.CancelledError:
                        continue
                if not tarefa.cancelled() and tarefa.exception() is not None:
                    logger.warning("fala cancelada terminou com erro: %r", tarefa.exception())
                raise
            finally:
                self.falando = False
                self.fim_ultima_fala = time.monotonic()

    def falou_desde(self, instante: float, rastro_s: float) -> bool:
        """True se ele estava falando a partir de `instante` (monotonic),
        contando `rastro_s` de audio ainda em transito depois de calar."""
        return self.falando or self.fim_ultima_fala + rastro_s > instante
