"""Transcricao de fala com faster-whisper (Cap 9 secao 3, passo 5).

O modelo Whisper exige audio mono a 16kHz - nao ha parametro de taxa de
amostragem no `transcribe()` da lib, entao quem grava (captura_audio.py ou
ouvidos_esp32.py) ja entrega 16000 para casar com essa exigencia.

O PORTAO ANTI-INVENCAO (2026-07-29). Pedido do dono, com razao:
*"quando o whisper nao entende ele inventa, isso e ruim"*. Um transcritor que
fabrica texto e PIOR que um que diz "nao entendi", porque o robo age sobre a
invencao - decide, fala e se move a partir de uma frase que ninguem disse.

Provado no mesmo dia: sobre um audio que dois outros modelos classificaram
como sem voz, este Whisper devolveu `'O que e isso? O que e isso?'` - frase
que esta gravada no banco desde 2026-07-25. Ele nao ouviu: ele REPETIU o
passado. A causa tem nome, e e o default da lib
(`condition_on_previous_text=True`), que alimenta o modelo com o que ele
mesmo disse antes e o faz continuar inventando na mesma linha.

Ate esta data a chamada era `transcribe(audio, language=...)` e mais nada -
todos os freios que a lib oferece estavam desligados. Agora:

- `vad_filter`: um detector de voz (Silero) corta os trechos sem fala ANTES
  de o modelo ver. E o freio mais eficaz: sem audio de ruido para
  interpretar, nao ha o que inventar.
- `condition_on_previous_text=False`: cada janela e julgada sozinha. Mata a
  repeticao de frases antigas.
- `temperature=0`: sem amostragem criativa. Silencio nao vira poesia.
- `no_speech_threshold` / `log_prob_threshold`: o proprio modelo estima
  "isto nao e fala" e "nao estou confiante". Segmento que falha em qualquer
  um dos dois e DESCARTADO aqui, explicitamente - preferimos devolver
  string vazia, que o VoiceCore ja trata como "nao entendi".
- `hotwords`: diz ao modelo que a palavra "Fofao" e esperada. Nao inventa
  nada: so desempata a favor dela quando o som esta no limite.
- `compression_ratio_threshold`: texto que comprime demais e repetitivo
  demais para ser fala. **Desde 2026-08-05 quem aplica este e o `_aceitos`,
  nao a lib** - ver o comentario na chamada do `transcribe()`.

O QUE ISTO NAO CONSERTA, dito para ninguem se enganar: se o audio chegar com
a voz afogada em chiado (medido em 2026-07-29 nos mics do ESP32: o pico dobra
quando o robo fala, mas o rms nao muda), o resultado correto passa a ser
string VAZIA em vez de frase inventada. Isso e uma melhora de honestidade,
nao de audicao - o robo passa a admitir que nao entendeu em vez de responder
a uma frase fantasma. Ouvir de verdade depende de sinal, nao de software.
"""
from __future__ import annotations

import asyncio
import logging
import zlib

import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger("orion.voice.transcricao")

#: Acima disto o proprio modelo considera que o trecho NAO e fala.
NO_SPEECH_THRESHOLD_PADRAO = 0.6
#: Abaixo disto o modelo esta "adivinhando" - log-probabilidade media por
#: token. -1.0 e o default da lib e ja descarta o pior lixo.
LOG_PROB_THRESHOLD_PADRAO = -1.0
#: Texto muito repetitivo (razao de compressao alta) e a assinatura classica
#: da alucinacao em laco ("E ai? E ai? E ai?...").
COMPRESSION_RATIO_THRESHOLD_PADRAO = 2.4


def razao_de_compressao(texto: str) -> float:
    """Quanto o texto encolhe ao ser comprimido - alto = repetitivo.

    Mesma formula que a lib usa (`get_compression_ratio`), reescrita aqui em
    tres linhas porque ela e um utilitario interno do faster-whisper: importar
    deixaria o portao dependendo de um nome que a lib pode mover sem aviso.
    """
    bytes_do_texto = texto.encode("utf-8")
    return len(bytes_do_texto) / len(zlib.compress(bytes_do_texto))


class Transcritor:
    def __init__(
        self,
        modelo: str = "base",
        idioma: str = "pt",
        vad_filter: bool = True,
        no_speech_threshold: float = NO_SPEECH_THRESHOLD_PADRAO,
        log_prob_threshold: float = LOG_PROB_THRESHOLD_PADRAO,
        compression_ratio_threshold: float = COMPRESSION_RATIO_THRESHOLD_PADRAO,
        hotwords: str | None = None,
    ) -> None:
        self._modelo = WhisperModel(modelo, device="cpu", compute_type="int8")
        self._idioma = idioma
        self._vad_filter = vad_filter
        self._no_speech_threshold = no_speech_threshold
        self._log_prob_threshold = log_prob_threshold
        self._compression_ratio_threshold = compression_ratio_threshold
        self._hotwords = hotwords

    async def transcrever(self, audio: np.ndarray) -> str:
        def _transcrever() -> str:
            segmentos, _ = self._modelo.transcribe(
                audio,
                language=self._idioma,
                vad_filter=self._vad_filter,
                # Cada janela julgada sozinha: e este parametro que fazia o
                # modelo repetir frases de conversas anteriores.
                condition_on_previous_text=False,
                temperature=0,
                no_speech_threshold=self._no_speech_threshold,
                log_prob_threshold=self._log_prob_threshold,
                # ATENCAO (2026-08-05): passar isto aqui NAO rejeita nada.
                # Dentro da lib o limiar so marca "tenta de novo com
                # temperatura maior"; como `temperature=0` e um numero unico,
                # a lista de tentativas tem um item so, o `for` acaba e o
                # `else` FICA COM O TEXTO REPETITIVO MESMO ASSIM
                # (faster_whisper/transcribe.py, o bloco do `needs_fallback`).
                # Quem de fato rejeita por repeticao e o `_aceitos` abaixo.
                compression_ratio_threshold=self._compression_ratio_threshold,
                hotwords=self._hotwords,
            )
            return " ".join(self._aceitos(segmentos)).strip()

        return await asyncio.to_thread(_transcrever)

    def _aceitos(self, segmentos) -> list[str]:
        """Segundo portao, sobre o que o modelo devolveu.

        A lib aplica os limiares para decidir se a JANELA e silencio, mas
        ainda entrega segmentos individuais que ela mesma marcou como pouco
        confiaveis. Aqui eles sao descartados um por um, e o descarte vai para
        o log - saber que o robo preferiu calar vale tanto quanto o texto.

        A REPETICAO SO E BARRADA AQUI (2026-08-05). Os dois limiares antigos
        nao pegam laco: um laco bem articulado tem `no_speech_prob` baixa e
        `avg_logprob` ALTA - o modelo esta muito confiante do lixo que
        produziu. Medido no dia: em 905 janelas, 27 sairam em laco
        ("o que e o que e..." 70 vezes numa linha so) e passaram inteiras.
        """
        texto: list[str] = []
        for s in segmentos:
            sem_fala = getattr(s, "no_speech_prob", 0.0)
            confianca = getattr(s, "avg_logprob", 0.0)
            if sem_fala > self._no_speech_threshold:
                # info, nao debug (2026-08-09): seis falas reais sairam `''`
                # numa so noite e o journal nao dizia qual freio disparou.
                # Saber que o robo preferiu calar vale tanto quanto o texto.
                logger.info(
                    "descartado (prob de NAO ser fala %.2f): %r", sem_fala, s.text.strip()
                )
                continue
            if confianca < self._log_prob_threshold:
                logger.info(
                    "descartado (confianca %.2f abaixo de %.2f): %r",
                    confianca, self._log_prob_threshold, s.text.strip(),
                )
                continue
            razao = razao_de_compressao(s.text.strip())
            if razao > self._compression_ratio_threshold:
                logger.info(
                    "descartado (repetitivo, razao %.2f acima de %.2f): %.60s...",
                    razao, self._compression_ratio_threshold, s.text.strip(),
                )
                continue
            texto.append(s.text.strip())
        return texto
