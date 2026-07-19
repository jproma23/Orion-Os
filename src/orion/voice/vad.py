"""Detector de atividade sonora (VAD) por energia, com piso de ruido
adaptativo (Cap 9 s.3).

Motivacao (medido em 2026-07-19): a vigilancia da palavra de ativacao
rodava o Whisper em TODA janela de escuta, continuamente - ~165% de CPU no
Notebook mesmo com a sala em silencio (load average 4.0 em 4 nucleos).
Este detector e o "porteiro" barato na frente do Whisper: mede a energia
(RMS) de cada janela em microssegundos e so deixa passar para a
transcricao quando ha som acima do ruido de fundo da sala. No silencio
(a maior parte do tempo), o Whisper nem e chamado.

Como funciona o piso adaptativo: guardamos o RMS das ultimas N janelas e
tomamos um percentil baixo como estimativa do ruido de fundo - mesmo que
haja fala em algumas janelas do historico, o percentil baixo continua
representando o silencio. Uma janela e "com som" quando o RMS dela passa
de `fator_acima_do_ruido` vezes esse piso (com um minimo absoluto
`rms_minimo` para nao disparar com o piso proximo de zero em salas muito
silenciosas).

Limitacao consciente: energia nao distingue fala de outros barulhos (TV,
palmas). Um barulho alto ainda acorda o Whisper - como hoje; o ganho e o
silencio ficar barato. O upgrade futuro e um modelo de wake word treinado
("Fofao", openWakeWord), ver wake_word.py.
"""
from __future__ import annotations

import logging
from collections import deque

import numpy as np

logger = logging.getLogger("orion.voice.vad")


class DetectorAtividadeSonora:
    """Decide se uma janela de audio tem som relevante ou so ruido de fundo."""

    def __init__(
        self,
        fator_acima_do_ruido: float = 2.5,
        rms_minimo: float = 0.003,
        janelas_de_historico: int = 30,
        percentil_do_piso: float = 20.0,
    ) -> None:
        self._fator = fator_acima_do_ruido
        self._rms_minimo = rms_minimo
        self._percentil = percentil_do_piso
        self._historico_rms: deque[float] = deque(maxlen=janelas_de_historico)
        self._janelas_puladas = 0

    @property
    def janelas_puladas(self) -> int:
        """Total de janelas silenciosas que pouparam uma transcricao."""
        return self._janelas_puladas

    def piso_de_ruido(self) -> float:
        """Estimativa atual do RMS do ruido de fundo da sala."""
        if not self._historico_rms:
            return 0.0
        return float(np.percentile(list(self._historico_rms), self._percentil))

    def tem_som(self, audio: np.ndarray) -> bool:
        """True se a janela tem som acima do ruido de fundo (vale a pena
        transcrever); False se e silencio/ruido (pular o Whisper)."""
        rms = float(np.sqrt(np.mean(np.square(np.asarray(audio, dtype="float64")))))
        piso = self.piso_de_ruido()
        limiar = max(piso * self._fator, self._rms_minimo)
        # O RMS entra no historico ANTES da decisao nao fazer diferenca para
        # esta janela (o percentil baixo ignora picos), e garante que o piso
        # exista ja na primeira janela.
        self._historico_rms.append(rms)

        com_som = rms > limiar
        if not com_som:
            self._janelas_puladas += 1
            logger.debug(
                "janela silenciosa pulada (rms=%.4f, piso=%.4f, limiar=%.4f, total puladas=%d)",
                rms,
                piso,
                limiar,
                self._janelas_puladas,
            )
        return com_som
