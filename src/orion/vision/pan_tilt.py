"""Calculo de correcao Pan/Tilt (Cap 8 secao 8).

O Vision Core so calcula os angulos - a execucao fisica e do Arduino
(Hardware Core), comandada pela cadeia Notebook -> Raspberry -> Arduino
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

    def calcular_correcao(
        self,
        centro_alvo_x: float,
        centro_alvo_y: float,
        largura_frame: int,
        altura_frame: int,
        intervalo_s: float,
    ) -> tuple[float, float]:
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
