"""Para onde a camera do pan/tilt olha (2026-11-08, pedido do dono).

Dois comportamentos, escolhidos pelo dono para ele "realmente enxergar":

1. MIRAR O ROSTO, NAO OS PES. A camera fica a ~20 cm do chao. Mirando o
   centro da caixa da pessoa, a cabeca centralizava a barriga ou as pernas e
   ficava olhando para baixo - o reconhecimento facial nunca via rosto. Agora
   a mira e o centro do rosto, quando o reconhecimento achou um; senao, o
   alto da caixa da pessoa, onde a cabeca dela deve estar. Com so as pernas
   no quadro (caixa cortada no topo), essa mira fica na borda de cima e a
   correcao ergue a camera ate a pessoa inteira entrar.

2. PROCURAR QUANDO NAO VE. Sem ninguem no quadro a cabeca ficava parada onde
   estava, muitas vezes olhando o chao. Agora, depois de alguns segundos sem
   alvo, ela varre devagar de um lado ao outro com a camera um pouco erguida,
   e para assim que alguem aparece (o rastreamento volta a mandar).

Este modulo so CALCULA. Quem decide se pode mexer (modo MANUAL, outro dono
mexendo na cabeca) e o VisionCore - ver `_talvez_procurar`.
"""
from __future__ import annotations

from dataclasses import dataclass

Caixa = tuple[float, float, float, float]  # x1, y1, x2, y2 (esquerda, topo, direita, base)


@dataclass(frozen=True)
class ConfigOlhar:
    """`vision.olhar` no orion.yaml. Os comentarios de cada valor moram la."""

    mirar_no_rosto: bool = True
    fracao_altura_cabeca: float = 0.15
    procurar: bool = True
    procurar_apos_s: float = 6.0
    pan_procura_graus: float = 60.0
    tilt_procura_graus: float = 15.0
    velocidade_procura_graus_s: float = 15.0
    ceder_a_outro_dono_s: float = 10.0

    @classmethod
    def de_dict(cls, dados: dict | None) -> ConfigOlhar:
        dados = dados or {}
        campos = cls.__dataclass_fields__
        return cls(**{chave: valor for chave, valor in dados.items() if chave in campos})


def _sobreposicao(a: Caixa, b: Caixa) -> float:
    largura = min(a[2], b[2]) - max(a[0], b[0])
    altura = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, largura) * max(0.0, altura)


def ponto_de_mira(
    caixa_pessoa: Caixa, caixas_rostos: list[Caixa], fracao_altura_cabeca: float
) -> tuple[float, float]:
    """Onde a camera deve centralizar para ver o ROSTO da pessoa.

    O rosto que mais se sobrepoe a pessoa ganha; sem rosto, a mira e o alto
    da caixa (`fracao_altura_cabeca` da altura, a partir do topo). O x da
    caixa da pessoa e mantido nesse caso: o corpo diz bem para que lado ela
    esta, o que falta e a altura.
    """
    melhor: Caixa | None = None
    melhor_area = 0.0
    for rosto in caixas_rostos:
        area = _sobreposicao(caixa_pessoa, rosto)
        if area > melhor_area:
            melhor, melhor_area = rosto, area
    if melhor is not None:
        return ((melhor[0] + melhor[2]) / 2, (melhor[1] + melhor[3]) / 2)
    x1, y1, x2, y2 = caixa_pessoa
    return ((x1 + x2) / 2, y1 + (y2 - y1) * fracao_altura_cabeca)


class Procura:
    """Varredura lenta de um lado ao outro, com a camera erguida.

    Sem estado de "onde estou": parte sempre da posicao atual dos servos
    (a que o VisionCore mantem e ressincroniza quando outro dono mexe), entao
    retomar a procura depois de uma interrupcao nao da salto.
    """

    def __init__(self, config: ConfigOlhar) -> None:
        self._config = config
        self._sentido = 1.0

    def proximo(
        self, pan_atual: float, tilt_atual: float, intervalo_s: float
    ) -> tuple[float, float]:
        passo = self._config.velocidade_procura_graus_s * max(0.0, intervalo_s)
        limite = abs(self._config.pan_procura_graus)

        if pan_atual >= limite:
            self._sentido = -1.0
        elif pan_atual <= -limite:
            self._sentido = 1.0
        pan = max(-limite, min(limite, pan_atual + self._sentido * passo))

        alvo_tilt = self._config.tilt_procura_graus
        diferenca = max(-passo, min(passo, alvo_tilt - tilt_atual))
        return pan, tilt_atual + diferenca
