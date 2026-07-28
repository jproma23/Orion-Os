"""Rastreamento de um alvo unico (Cap 8 secao 3, 6).

Escolhe uma caixa entre as deteccoes de cada frame para ser "o alvo":
a mais proxima da posicao anterior (rastreio continuo) ou, sem alvo
anterior, a maior caixa (pessoa mais perto da camera). Sem nenhuma
deteccao por `timeout_perdido_s`, o alvo e considerado perdido.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

Caixa = tuple[int, int, int, int]


@dataclass
class Alvo:
    caixa: Caixa
    pessoa_id: int | None
    nome: str | None
    ultima_atualizacao: float


def _centro(caixa: Caixa) -> tuple[float, float]:
    x1, y1, x2, y2 = caixa
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _area(caixa: Caixa) -> int:
    x1, y1, x2, y2 = caixa
    return max(0, x2 - x1) * max(0, y2 - y1)


def _maior_caixa(caixas: list[Caixa]) -> Caixa:
    return max(caixas, key=_area)


def _caixa_mais_proxima(caixas: list[Caixa], referencia: Caixa) -> Caixa:
    cx, cy = _centro(referencia)

    def _distancia_quadrada(caixa: Caixa) -> float:
        ax, ay = _centro(caixa)
        return (ax - cx) ** 2 + (ay - cy) ** 2

    return min(caixas, key=_distancia_quadrada)


def _iou(a: Caixa, b: Caixa) -> float:
    """Intersection over Union: 0 (nao se tocam) a 1 (identicas)."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    intersecao = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if intersecao == 0:
        return 0.0
    uniao = _area(a) + _area(b) - intersecao
    return intersecao / uniao if uniao > 0 else 0.0


class Rastreador:
    def __init__(self, timeout_perdido_s: float = 2.0, frames_confirmacao: int = 1) -> None:
        self._timeout_perdido_s = timeout_perdido_s
        # Exige a MESMA regiao detectada em N quadros seguidos antes de
        # assumir um alvo novo (default 1 = sem exigencia, compatível com o
        # comportamento anterior). Mitigacao para falso positivo do YOLO
        # (2026-07-28): um objeto/sombra confundido com "pessoa" por um unico
        # quadro nao chegava a durar o suficiente para virar alvo de verdade,
        # mas ainda assim fazia o pan/tilt saltar para aquela posicao - a
        # correcao NAO exige uma pessoa real parada, so exige que a MESMA
        # deteccao (IoU > 0.5) se repita por `frames_confirmacao` quadros.
        self._frames_confirmacao = max(1, frames_confirmacao)
        self._alvo: Alvo | None = None
        self._candidata: Caixa | None = None
        self._candidata_contagem = 0

    def atualizar(
        self,
        caixas_candidatas: list[Caixa],
        identidades: dict[Caixa, tuple[int | None, str | None]] | None = None,
        agora: float | None = None,
    ) -> Alvo | None:
        agora = agora if agora is not None else time.monotonic()
        identidades = identidades or {}

        if not caixas_candidatas:
            self._candidata = None
            self._candidata_contagem = 0
            if self._alvo is not None and (agora - self._alvo.ultima_atualizacao) > self._timeout_perdido_s:
                self._alvo = None
            return self._alvo

        if self._alvo is not None:
            escolhida = _caixa_mais_proxima(caixas_candidatas, self._alvo.caixa)
            pessoa_id, nome = identidades.get(escolhida, (None, None))
            self._alvo = Alvo(caixa=escolhida, pessoa_id=pessoa_id, nome=nome, ultima_atualizacao=agora)
            return self._alvo

        candidata = _maior_caixa(caixas_candidatas)
        if self._candidata is not None and _iou(candidata, self._candidata) > 0.5:
            self._candidata_contagem += 1
        else:
            self._candidata = candidata
            self._candidata_contagem = 1

        if self._candidata_contagem < self._frames_confirmacao:
            return None

        pessoa_id, nome = identidades.get(candidata, (None, None))
        self._alvo = Alvo(caixa=candidata, pessoa_id=pessoa_id, nome=nome, ultima_atualizacao=agora)
        self._candidata = None
        self._candidata_contagem = 0
        return self._alvo

    @property
    def alvo_atual(self) -> Alvo | None:
        return self._alvo

    def perdido(self, agora: float | None = None) -> bool:
        if self._alvo is None:
            return True
        agora = agora if agora is not None else time.monotonic()
        return (agora - self._alvo.ultima_atualizacao) > self._timeout_perdido_s
