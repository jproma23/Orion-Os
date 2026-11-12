"""Giro do robo medido pela CAMERA - a testemunha da odometria (2026-11-12).

POR QUE EXISTE. A pose do Pi vem do encoder da roda (`fusao_sensores.py`), e o
encoder mede a RODA, nao o chao: roda no ar, patinando ou com o rele aberto
"anda" na pose e fica parada no mundo. Ja aconteceu (30/07: 85 cm e 80 graus
afirmados com os motores desconectados). A bussola, que deveria desmentir o
giro, esta congelada no corpo atual. A camera e o unico sensor que olha o
MUNDO girar - o pedido do dono foi "o YOLO precisa usar a camera e usar na
odometria".

O QUE O YOLO FAZ AQUI. O YOLO nao mede movimento; ele diz ONDE ha coisa que se
mexe sozinha. Pessoa andando na frente da lente desloca a imagem e pareceria
"o robo girou". Por isso as caixas de pessoas e animais (`CLASSES_DINAMICAS`)
sao APAGADAS do quadro antes da medida: so o cenario parado conta.

COMO MEDE. Correlacao de fase (`cv2.phaseCorrelate`) entre o quadro anterior e
o atual, em cinza reduzido a 160x120: devolve o deslocamento global da imagem
em pixels e uma RESPOSTA (0-1) que diz o quanto a medida e confiavel. Robo
girando no proprio eixo = a imagem inteira desliza na horizontal. Conteudo que
foi para a DIREITA = a camera virou para a ESQUERDA = giro positivo
(anti-horario, a mesma convencao do yaw do Pi). Medido no teste sintetico:
12 px para a direita no quadro cheio -> dx = +2,95 na escala reduzida.

O QUE ELA NAO MEDE, e o que fazer com isso:

- DISTANCIA. Andar para a frente abre a imagem do centro para fora (sem
  deslizar), e camera sozinha nao tem escala. O que sai daqui e so a
  `mudanca` da imagem (diferenca media depois de alinhar) - serve de
  testemunha de "a cena mudou", nunca de centimetros.
- CABECA MEXENDO. Se o pan/tilt mudou entre os dois quadros, o deslizamento
  e da cabeca, nao do corpo. O quadro e marcado INVALIDO (nao se subtrai: a
  posicao dos servos e estimada, nao medida).
- PAREDE LISA / ESCURO. Resposta baixa (parede lisa deu 0,08 no teste, com
  deslocamento lixo de 57 px) = quadro invalido. Melhor nao saber que mentir.
- GIRO RAPIDO DEMAIS. A visao roda a `vision.fps` (2/s). Entre dois quadros
  a imagem tem que se sobrepor; acima de ~1/3 do campo de visao por quadro a
  correlacao perde o fio e a resposta cai - vira invalido, nao numero errado.

O NUMERO QUE FALTA MEDIR: `fov_horizontal_graus` (campo de visao horizontal
da DV20). Ele converte pixel em grau. O valor do yaml e ESTIMATIVA ate a
bancada (girar 90 graus medidos e ver quanto a camera diz). Por isso o peso
da camera na fusao do Pi nasce 0: ela testemunha, mas nao corrige a pose,
ate esse numero ser medido.

Saida: `vision.giro_visual` a cada quadro processado, com o ACUMULADO (e nao
so o delta) para o Pi tolerar mensagem perdida no TCP: ele subtrai dois
acumulados. Os quadros invalidos entram num contador proprio, e o Pi so usa a
camera numa janela em que esse contador nao mudou.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import cv2
import numpy as np

#: Classes do YOLO que se mexem sozinhas - apagadas antes de medir.
CLASSES_DINAMICAS = frozenset(
    {
        "person", "bird", "cat", "dog", "horse", "sheep", "cow",
        "elephant", "bear", "zebra", "giraffe",
    }
)

#: Tamanho do quadro reduzido onde a medida e feita. 160x120 custa ~1 ms e
#: ainda tem textura de sobra num comodo.
LARGURA_REDUZIDA = 160
ALTURA_REDUZIDA = 120

#: Abaixo disto a correlacao nao achou deslocamento confiavel. Parede lisa
#: com ruido deu 0,08 no teste; cena texturizada com ruido de sensor, 0,86.
RESPOSTA_MINIMA_PADRAO = 0.2

#: Se as caixas dinamicas cobrem mais que isto do quadro, sobra pouco cenario
#: para medir - invalido.
FRACAO_MASCARADA_MAXIMA = 0.6

#: Mexida da cabeca (graus) entre dois quadros a partir da qual o quadro e
#: invalido. Servo nao resolve menos que ~1 grau.
CABECA_PARADA_GRAUS = 0.5

#: Deslocamento vertical (fracao da altura) acima do qual nao e giro do corpo:
#: robo girando no chao desliza na horizontal. Muito vertical = tilt mexeu,
#: tranco, ou correlacao errada.
DESLOCAMENTO_VERTICAL_MAXIMO = 0.15

#: Desvio padrao minimo (0-255, quadro reduzido) para haver o que medir.
#: Parede lisa com ruido de sensor fica em ~0,4; um comodo, em ~25.
TEXTURA_MINIMA = 2.0

FOV_HORIZONTAL_PADRAO_GRAUS = 60.0


@dataclass(frozen=True)
class MedidaGiro:
    """Resultado de um quadro."""

    valido: bool
    motivo: str | None
    delta_graus: float
    resposta: float
    mudanca: float | None


class OdometriaVisual:
    """Mede o giro entre quadros consecutivos e mantem o acumulado."""

    def __init__(
        self,
        fov_horizontal_graus: float = FOV_HORIZONTAL_PADRAO_GRAUS,
        resposta_minima: float = RESPOSTA_MINIMA_PADRAO,
    ) -> None:
        self._graus_por_pixel = float(fov_horizontal_graus) / LARGURA_REDUZIDA
        self._resposta_minima = float(resposta_minima)
        self._janela = cv2.createHanningWindow((LARGURA_REDUZIDA, ALTURA_REDUZIDA), cv2.CV_32F)
        self._anterior: np.ndarray | None = None
        self._cabeca_anterior: tuple[float, float] | None = None
        self.acumulado_graus = 0.0
        self.quadros_validos = 0
        self.quadros_invalidos = 0

    def medir(
        self,
        frame: np.ndarray,
        caixas_dinamicas: Iterable[tuple[int, int, int, int]] = (),
        cabeca: tuple[float, float] = (0.0, 0.0),
    ) -> MedidaGiro:
        """Mede o giro desde o quadro anterior.

        `caixas_dinamicas`: caixas (x1, y1, x2, y2, pixels do frame ORIGINAL)
        de quem se mexe sozinho. `cabeca`: (pan, tilt) assumidos neste quadro.
        O primeiro quadro so vira base (invalido, motivo "primeiro_quadro").
        """
        reduzido, fracao_mascarada = self._preparar(frame, caixas_dinamicas)
        anterior, self._anterior = self._anterior, reduzido
        cabeca_anterior, self._cabeca_anterior = self._cabeca_anterior, cabeca

        if anterior is None:
            return self._invalido("primeiro_quadro")
        if cabeca_anterior is not None and (
            abs(cabeca[0] - cabeca_anterior[0]) > CABECA_PARADA_GRAUS
            or abs(cabeca[1] - cabeca_anterior[1]) > CABECA_PARADA_GRAUS
        ):
            return self._invalido("cabeca_mexeu")
        if fracao_mascarada > FRACAO_MASCARADA_MAXIMA:
            return self._invalido("pouco_cenario")

        if float(reduzido.std()) < TEXTURA_MINIMA or float(anterior.std()) < TEXTURA_MINIMA:
            return self._invalido("sem_textura")

        (dx, dy), resposta = cv2.phaseCorrelate(anterior, reduzido, self._janela)
        resposta = float(resposta)
        if resposta < self._resposta_minima:
            return self._invalido("sem_textura", resposta)
        if abs(dy) > DESLOCAMENTO_VERTICAL_MAXIMO * ALTURA_REDUZIDA:
            return self._invalido("deslocamento_vertical", resposta)

        mudanca = self._mudanca_alinhada(anterior, reduzido, dx, dy)
        delta = float(dx) * self._graus_por_pixel
        self.acumulado_graus += delta
        self.quadros_validos += 1
        return MedidaGiro(True, None, round(delta, 2), round(resposta, 3), round(mudanca, 2))

    def quebrar(self) -> None:
        """Esquece o quadro-base: houve buraco (camera sem sinal, erro de
        leitura, visao pausada). Comparar com um quadro de segundos atras
        mediria o giro inteiro do buraco como se fosse um passo - e o Pi
        precisa saber que a janela quebrou, por isso conta como invalido."""
        if self._anterior is not None:
            self._anterior = None
            self._cabeca_anterior = None
            self.quadros_invalidos += 1

    def _invalido(self, motivo: str, resposta: float = 0.0) -> MedidaGiro:
        # O primeiro quadro nao conta como invalido: nao ha janela quebrada,
        # so ainda nao havia base.
        if motivo != "primeiro_quadro":
            self.quadros_invalidos += 1
        return MedidaGiro(False, motivo, 0.0, round(resposta, 3), None)

    @staticmethod
    def _preparar(frame, caixas) -> tuple[np.ndarray, float]:
        imagem = np.asarray(frame)
        if imagem.ndim == 3:
            imagem = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
        imagem = imagem.astype(np.float32)
        altura, largura = imagem.shape[:2]
        mascara = np.zeros((altura, largura), dtype=bool)
        for x1, y1, x2, y2 in caixas:
            x1, x2 = max(0, int(x1)), min(largura, int(x2))
            y1, y2 = max(0, int(y1)), min(altura, int(y2))
            if x2 > x1 and y2 > y1:
                mascara[y1:y2, x1:x2] = True
        fracao = float(mascara.mean()) if mascara.size else 0.0
        if mascara.any() and not mascara.all():
            # Apaga com a media do CENARIO (nao com preto): borda forte
            # inventada viraria textura e puxaria a correlacao para ela.
            imagem = imagem.copy()
            imagem[mascara] = float(imagem[~mascara].mean())
        reduzido = cv2.resize(
            imagem, (LARGURA_REDUZIDA, ALTURA_REDUZIDA), interpolation=cv2.INTER_AREA
        )
        # MEDIA ZERO (achado no teste de 12/11): sem isto, a janela de Hanning
        # sobre uma parede lisa vira um "morro" identico nos dois quadros, que
        # correlaciona consigo mesmo - resposta 0,62 e giro 0: "certeza de que
        # parou" de frente para uma parede, girando ou nao. Com a media
        # tirada, a mesma parede da 0,03 e cai no limiar.
        reduzido -= float(reduzido.mean())
        return reduzido, fracao

    @staticmethod
    def _mudanca_alinhada(anterior, atual, dx: float, dy: float) -> float:
        """Diferenca media depois de desfazer o deslizamento: o quanto a cena
        mudou ALEM do giro (andar para a frente, coisa mexendo)."""
        matriz = np.float32([[1, 0, -dx], [0, 1, -dy]])
        alinhado = cv2.warpAffine(
            atual, matriz, (LARGURA_REDUZIDA, ALTURA_REDUZIDA), borderMode=cv2.BORDER_REPLICATE
        )
        borda_x = min(LARGURA_REDUZIDA // 2 - 1, int(abs(dx)) + 2)
        borda_y = min(ALTURA_REDUZIDA // 2 - 1, int(abs(dy)) + 2)
        recorte = (
            slice(borda_y, ALTURA_REDUZIDA - borda_y),
            slice(borda_x, LARGURA_REDUZIDA - borda_x),
        )
        return float(np.abs(alinhado[recorte] - anterior[recorte]).mean())

    def evento(self, medida: MedidaGiro, timestamp: float) -> dict:
        """Payload de `vision.giro_visual`."""
        return {
            "valido": medida.valido,
            "motivo": medida.motivo,
            "delta_graus": medida.delta_graus,
            "acumulado_graus": round(self.acumulado_graus, 2),
            "quadros_validos": self.quadros_validos,
            "quadros_invalidos": self.quadros_invalidos,
            "resposta": medida.resposta,
            "mudanca": medida.mudanca,
            "timestamp": timestamp,
        }
