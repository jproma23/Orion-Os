"""O timbre do Jarvis - o que faz a voz dele ser DELE.

## Por que isto existe

A sintese usava `pt_BR-faber-medium` cru, que e a voz padrao de portugues do
Brasil no Piper. Boa, e exatamente igual a de qualquer outro projeto que use
Piper em pt_BR. Pedido do dono em 2026-08-03: uma voz propria, **calorosa e
presente**.

## "Presente" significa MENOS reverb, nao mais

E a decisao que orienta o resto. A tentacao ao pedir "voz marcante" e jogar
reverb, porque e o efeito mais audivel. Mas reverb simula distancia: quanto
mais, mais longe a voz parece. Presenca se faz pelo contrario - proximidade,
corpo nos medios-graves e faixa dinamica estreita, que e como soa alguem
falando perto de voce.

Por isso aqui NAO ha reverb nenhum. Ha tres coisas:

1. **Calor** - realce suave abaixo de ~350 Hz, que da corpo a voz.
2. **Menos aspereza** - queda gradual acima de ~7 kHz. O Piper produz um
   sibilo digital nos agudos que e o que mais denuncia "isto e uma maquina".
3. **Presenca** - compressao leve. Estreitar a dinamica faz a voz soar mais
   proxima e constante, sem precisar aumentar o volume.

## Por que FFT e nao filtro recursivo

Um filtro de um polo (`y[n] = y[n-1] + a*(x[n]-y[n-1])`) e o jeito classico,
mas em Python puro ele e um laco por AMOSTRA - dezenas de milhares por frase.
Isso custaria dezenas de milissegundos e este projeto brigou muito para baixar
a latencia da fala (3,50 s -> 0,66 s ate o primeiro som); nao vale gastar o
ganho em EQ. `scipy.signal.lfilter` resolveria, mas scipy nao e dependencia
deste projeto e nao vale trazer um pacote inteiro por causa disto.

A FFT faz o mesmo trabalho vetorizado, numa passada, com numpy que ja esta
aqui. Para um trecho de frase e mais rapido e o resultado e equivalente.

## Nada aqui inventa conteudo

Todo o processamento e sobre o audio que o Piper produziu. Se o tratamento
falhar por qualquer motivo, `aquecer()` devolve o audio original - a voz sai
sem tempero, mas sai. Robo mudo e pior que robo sem timbre.
"""
from __future__ import annotations

import numpy as np

#: Perfil "caloroso e presente", escolhido pelo dono em 2026-08-03.
#:
#: Os numeros sao conservadores de proposito: processamento de voz erra para o
#: lado do exagero muito facil, e voz sobreprocessada soa artificial - o
#: oposto do que se quer aqui. Preferir discreto a chamativo.
CALOR_DB = 3.0          #: realce nos medios-graves (corpo)
CORTE_CALOR_HZ = 350.0  #: abaixo disto o realce atua
SUAVIDADE_DB = 4.0      #: quanto tirar dos agudos asperos
CORTE_AGUDOS_HZ = 7000.0
PRESENCA = 0.5          #: 0 = sem compressao, 1 = bem comprimido
PICO_ALVO = 0.89        #: margem contra clipping depois do tratamento


def _curva_de_ganho(freqs: np.ndarray) -> np.ndarray:
    """Ganho por frequencia: corpo embaixo, doce em cima."""
    # Prateleira grave suave. A forma 1/(1+(f/fc)^2) vale ~1 no grave e cai
    # para 0 no agudo, entao ela LIGA o realce so onde interessa, sem degrau -
    # degrau em EQ vira ressonancia audivel.
    calor = 1.0 + (10 ** (CALOR_DB / 20) - 1.0) / (1.0 + (freqs / CORTE_CALOR_HZ) ** 2)
    # Queda gradual dos agudos, tambem sem degrau.
    suave = 1.0 - (1.0 - 10 ** (-SUAVIDADE_DB / 20)) / (
        1.0 + (CORTE_AGUDOS_HZ / np.maximum(freqs, 1.0)) ** 2
    )
    return calor * suave


def _equalizar(x: np.ndarray, taxa: int) -> np.ndarray:
    espectro = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, 1.0 / taxa)
    return np.fft.irfft(espectro * _curva_de_ganho(freqs), n=x.size)


def _comprimir(x: np.ndarray, quanto: float, taxa: int) -> np.ndarray:
    """Compressao descendente leve, para a voz soar proxima e constante.

    O envelope sai de uma media movel do valor absoluto (~25 ms), feita com
    convolucao - vetorizada, sem laco por amostra.
    """
    if quanto <= 0:
        return x
    janela = max(1, int(0.025 * taxa))
    envelope = np.convolve(np.abs(x), np.ones(janela) / janela, mode="same")
    limiar = 0.35 * float(np.max(envelope) or 1.0)
    # Onde o envelope passa do limiar, baixa o ganho. `quanto` interpola entre
    # nao mexer (0) e razao ~3:1 (1).
    excesso = np.maximum(envelope, limiar) / limiar
    return x * excesso ** (-quanto * 0.66)


def aquecer(audio: np.ndarray, taxa: int) -> np.ndarray:
    """Aplica o timbre do Jarvis. Devolve int16, pronto para tocar.

    Recebe e devolve `int16` porque e o formato que sai do Piper e o que o
    `sounddevice` toca - converter aqui evita que quem chama precise saber de
    ponto flutuante.
    """
    if audio.size == 0:
        return audio
    try:
        x = audio.astype(np.float32) / 32768.0
        x = _equalizar(x, taxa)
        x = _comprimir(x, PRESENCA, taxa)
        pico = float(np.max(np.abs(x)))
        if pico > 0:
            x = x * (PICO_ALVO / pico)
        return np.clip(x * 32768.0, -32768, 32767).astype(np.int16)
    except Exception:
        # Voz sem tempero e aceitavel; robo mudo nao e.
        return audio
