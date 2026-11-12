"""Detecta a camera que entrega imagem mas nao enxerga nada (2026-11-01).

POR QUE EXISTE. Em 31/10 a visao ficou cega sem uma linha de erro: o stream
do Pi servia a tela "NO SIGNAL" com HTTP 200, o OpenCV lia quadros validos, o
YOLO rodava sobre ela e o diagnostico dizia camera ONLINE, frame OK,
inference RUNNING, detections 0. Do lado de fora, "nao estou vendo nada" e
"estou cego" saiam identicos. Antes disso a camera ja tinha passado dias
apontada para o teto.

Duas assinaturas, medidas no proprio quadro, valem para qualquer camera:

- **escuro e uniforme**: brilho medio baixo E quase sem variacao entre os
  pixels. E a lente tampada, a camera virada para uma superficie lisa no
  escuro, ou a tela preta de "sem sinal".
- **congelado**: dois quadros seguidos praticamente identicos. Uma camera de
  verdade tem ruido de sensor - mesmo olhando uma parede parada, nenhum pixel
  repete exato. Imagem que nao muda NADA e uma imagem fixa servida no lugar
  do video (a tela "NO SIGNAL" do ustreamer, um quadro preso no buffer).

Uma ocorrencia isolada nao basta: so declara "sem sinal" depois de
`quadros_seguidos` quadros consecutivos com uma das duas assinaturas, para um
quadro escuro de passagem (alguem passando na frente da lente) nao virar
alarme.

So usa numpy de proposito: o mesmo detector serve o Vision Core do Notebook e
o Vision Engine do Pi, e e testavel com arrays sinteticos, sem camera.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Brilho medio (0-255) abaixo do qual o quadro conta como escuro.
BRILHO_MAXIMO_ESCURO = 20.0
#: Desvio padrao do brilho abaixo do qual o quadro conta como uniforme.
DESVIO_MAXIMO_UNIFORME = 8.0
#: Diferenca media absoluta entre dois quadros abaixo da qual a imagem conta
#: como congelada. Uma imagem fixa reenviada (o mesmo JPEG) da EXATAMENTE 0.
#:
#: ERA 0,2, e 0,2 pegava camera boa (auditoria 12/11): cena parada + pouco
#: ruido de sensor + compressao MJPEG qualidade 80 deu diferenca media 0,13
#: (simulado: ruido 0,5; com ruido 1,0 deu 0,26 - margem de nada). A
#: compressao "come" o ruido nos blocos lisos, e uma sala parada de dia virava
#: "camera SEM SINAL": o YOLO parava de rodar e a resposta era "nao estou
#: vendo nada". 0,02 fica longe dos dois lados - o congelado de verdade e 0.
DIFERENCA_MAXIMA_CONGELADO = 0.02
#: Quantos quadros seguidos com a assinatura antes de declarar sem sinal. A
#: 2 quadros/s, 6 quadros = 3 s.
QUADROS_SEGUIDOS_PADRAO = 6


@dataclass(frozen=True)
class Avaliacao:
    """Resultado de um quadro: o que foi medido e a conclusao."""

    sem_sinal: bool
    motivo: str | None
    brilho_medio: float
    desvio: float
    diferenca_anterior: float | None


class DetectorSemSinal:
    """Acompanha os quadros e diz quando a camera deixou de enxergar."""

    def __init__(self, quadros_seguidos: int = QUADROS_SEGUIDOS_PADRAO) -> None:
        self._quadros_seguidos = max(1, int(quadros_seguidos))
        self._contagem = 0
        self._anterior: np.ndarray | None = None
        self._ultimo_motivo: str | None = None

    def avaliar(self, frame) -> Avaliacao:
        """Mede um quadro e devolve se a camera esta sem sinal AGORA."""
        cinza = self._cinza_reduzido(frame)
        brilho = float(cinza.mean())
        desvio = float(cinza.std())

        diferenca: float | None = None
        if self._anterior is not None and self._anterior.shape == cinza.shape:
            diferenca = float(np.abs(cinza - self._anterior).mean())
        self._anterior = cinza

        motivo: str | None = None
        if brilho < BRILHO_MAXIMO_ESCURO and desvio < DESVIO_MAXIMO_UNIFORME:
            motivo = "imagem_escura_uniforme"
        elif diferenca is not None and diferenca < DIFERENCA_MAXIMA_CONGELADO:
            motivo = "imagem_congelada"

        if motivo is None:
            self._contagem = 0
        else:
            self._contagem += 1
            self._ultimo_motivo = motivo

        sem_sinal = self._contagem >= self._quadros_seguidos
        return Avaliacao(
            sem_sinal=sem_sinal,
            motivo=self._ultimo_motivo if sem_sinal else None,
            brilho_medio=round(brilho, 1),
            desvio=round(desvio, 1),
            diferenca_anterior=None if diferenca is None else round(diferenca, 2),
        )

    @staticmethod
    def _cinza_reduzido(frame) -> np.ndarray:
        """Cinza em float, reduzido 4x em cada eixo - medir 640x480 inteiro
        a cada quadro seria custo sem ganho para medias e desvios."""
        imagem = np.asarray(frame)
        if imagem.ndim == 3:
            imagem = imagem.mean(axis=2)
        return imagem[::4, ::4].astype(np.float32)
