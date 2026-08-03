"""Calculo de correcao Pan/Tilt (Cap 8 secao 8).

O Vision Core so calcula os angulos - a execucao fisica e do Hardware Core
(Hardware Core), comandada pela cadeia Notebook -> Raspberry -> ESP32
(Cap 3, 5; EDR-0018) via o comando SET_PAN_TILT (extensao do Cap 10 s.5:
o hardware pan/tilt esta listado no Cap 10 s.2, mas o comando nao constava
na lista original - ver firmware/hardware_core/include/command_executor.h).
Limites de angulo/velocidade vem de config/orion.yaml (secao vision) - nao
sao fixos aqui, so tem um default se o chamador nao passar `LimitesPanTilt`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LimitesPanTilt:
    pan_min_graus: float = -80.0
    pan_max_graus: float = 80.0
    tilt_min_graus: float = -30.0
    tilt_max_graus: float = 45.0
    velocidade_max_graus_s: float = 60.0
    tolerancia_centralizado_percent: float = 5.0


class CalculadoraPanTilt:
    """Mantem a posicao assumida dos servos e calcula o proximo passo para
    centralizar o alvo, com controle proporcional simples (suficiente para
    o "minimo" desta fase - sem PID completo) respeitando limites de
    angulo e velocidade maxima."""

    def __init__(self, limites: LimitesPanTilt | None = None) -> None:
        self._limites = limites or LimitesPanTilt()
        self.pan_atual = 0.0
        self.tilt_atual = 0.0

    def sincronizar(self, pan: float, tilt: float) -> None:
        """Corrige a posicao ASSUMIDA quando OUTRO dono mexeu na cabeca.

        A cabeca tem dois donos independentes (achado em 2026-07-29): a visao
        manda SET_PAN_TILT direto pelo `comm.send`, e os comportamentos do Pi
        publicam `motion.pan_tilt` (Repouso nivela em 0,0; a ronda varre
        -60 -> +60 -> 0). Nenhum dos dois sabia do outro.

        Sem este metodo a correcao saia de uma base que nao existe: com a
        cabeca fisicamente em -60 e `pan_atual` valendo +20, a proxima
        correcao mandava +22 e a cabeca PULAVA 82 graus de uma vez. E piorava
        quanto mais tempo o sistema ficava de pe, porque cada varredura entre
        dois quadros desalinhava a estimativa outra vez.

        Os limites sao aplicados aqui tambem: se alguem mandou a cabeca para
        fora da faixa que esta calculadora conhece, a estimativa acompanha o
        que o servo consegue de fato fazer, nao o que foi pedido.
        """
        self.pan_atual = max(
            self._limites.pan_min_graus, min(self._limites.pan_max_graus, float(pan))
        )
        self.tilt_atual = max(
            self._limites.tilt_min_graus, min(self._limites.tilt_max_graus, float(tilt))
        )

    def calcular_correcao(
        self,
        centro_alvo_x: float,
        centro_alvo_y: float,
        largura_frame: int,
        altura_frame: int,
        intervalo_s: float,
    ) -> tuple[float, float]:
        # ZONA MORTA (2026-08-03). Relato do dono, com a camera do pan/tilt
        # finalmente entregando quadros de verdade: *"ele fica louco... em
        # menor escala"*.
        #
        # O QUE ACONTECIA: este metodo corrigia em TODO quadro, por menor que
        # fosse o erro. Com a pessoa parada e ja centralizada, um alvo que
        # oscila dois ou tres pixels entre quadros (respiracao, ruido da
        # caixa do YOLO, compressao do MJPEG) virava comando de servo a 2 Hz,
        # sem parar. Isso nao e seguir alguem: e tremer.
        #
        # A tolerancia que resolve JA EXISTIA na classe - `alvo_centralizado`
        # e `tolerancia_centralizado_percent` - mas so era usada por quem
        # PERGUNTAVA se o alvo estava centralizado, nunca por quem decidia
        # mover. O valor entrava na conta de ninguem.
        #
        # 5% do quadro sao +-32 px em 640x480. A pessoa continua enquadrada;
        # o que se perde e a perseguicao ao pixel, que nao servia para nada -
        # o objetivo e manter alguem NO QUADRO, nao no centro exato.
        #
        # Devolver a posicao atual (em vez de nao publicar) e de proposito:
        # quem chama publica `motion.pan_tilt` sempre, e o avatar espelha
        # essa direcao. Sumir com o evento faria a cabeca do avatar congelar
        # em vez de ficar parada onde esta - dois estados diferentes.
        if self.alvo_centralizado(centro_alvo_x, centro_alvo_y, largura_frame, altura_frame):
            return self.pan_atual, self.tilt_atual

        erro_x = (centro_alvo_x - largura_frame / 2) / (largura_frame / 2)
        erro_y = (centro_alvo_y - altura_frame / 2) / (altura_frame / 2)

        pan_alcance = (self._limites.pan_max_graus - self._limites.pan_min_graus) / 2
        tilt_alcance = (self._limites.tilt_max_graus - self._limites.tilt_min_graus) / 2

        pan_desejado = self.pan_atual + erro_x * pan_alcance * 0.3
        # y cresce para baixo na imagem - inclinar "para cima" quando o alvo esta acima do centro
        tilt_desejado = self.tilt_atual - erro_y * tilt_alcance * 0.3

        passo_maximo = self._limites.velocidade_max_graus_s * intervalo_s
        self.pan_atual = self._mover_no_limite(
            self.pan_atual, pan_desejado, passo_maximo,
            self._limites.pan_min_graus, self._limites.pan_max_graus,
        )
        self.tilt_atual = self._mover_no_limite(
            self.tilt_atual, tilt_desejado, passo_maximo,
            self._limites.tilt_min_graus, self._limites.tilt_max_graus,
        )
        return self.pan_atual, self.tilt_atual

    def alvo_centralizado(
        self, centro_alvo_x: float, centro_alvo_y: float, largura_frame: int, altura_frame: int
    ) -> bool:
        tolerancia_x = largura_frame * (self._limites.tolerancia_centralizado_percent / 100)
        tolerancia_y = altura_frame * (self._limites.tolerancia_centralizado_percent / 100)
        return (
            abs(centro_alvo_x - largura_frame / 2) <= tolerancia_x
            and abs(centro_alvo_y - altura_frame / 2) <= tolerancia_y
        )

    @staticmethod
    def _mover_no_limite(
        atual: float, desejado: float, passo_maximo: float, minimo: float, maximo: float
    ) -> float:
        diferenca = max(-passo_maximo, min(passo_maximo, desejado - atual))
        return max(minimo, min(maximo, atual + diferenca))
