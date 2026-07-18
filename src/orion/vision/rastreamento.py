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


class Rastreador:
    def __init__(self, timeout_perdido_s: float = 2.0) -> None:
        self._timeout_perdido_s = timeout_perdido_s
        self._alvo: Alvo | None = None

    def atualizar(
        self,
        caixas_candidatas: list[Caixa],
        identidades: dict[Caixa, tuple[int | None, str | None]] | None = None,
        agora: float | None = None,
    ) -> Alvo | None:
        agora = agora if agora is not None else time.monotonic()
        identidades = identidades or {}

        if not caixas_candidatas:
            if self._alvo is not None and (agora - self._alvo.ultima_atualizacao) > self._timeout_perdido_s:
                self._alvo = None
            return self._alvo

        escolhida = (
            _maior_caixa(caixas_candidatas)
            if self._alvo is None
            else _caixa_mais_proxima(caixas_candidatas, self._alvo.caixa)
        )
        pessoa_id, nome = identidades.get(escolhida, (None, None))
        self._alvo = Alvo(caixa=escolhida, pessoa_id=pessoa_id, nome=nome, ultima_atualizacao=agora)
        return self._alvo

    @property
    def alvo_atual(self) -> Alvo | None:
        return self._alvo

    def perdido(self, agora: float | None = None) -> bool:
        if self._alvo is None:
            return True
        agora = agora if agora is not None else time.monotonic()
        return (agora - self._alvo.ultima_atualizacao) > self._timeout_perdido_s
