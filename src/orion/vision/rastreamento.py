"""Rastreamento de um alvo unico (Cap 8 secao 3, 6).

Escolhe uma caixa entre as deteccoes de cada frame para ser "o alvo":
a mais proxima da posicao anterior (rastreio continuo) ou, sem alvo
anterior, a maior caixa (pessoa mais perto da camera). Sem nenhuma
deteccao por `timeout_perdido_s`, o alvo e considerado perdido.

O ALVO CONHECIDO E SEGUIDO PELO ROSTO (auditoria 12/11, dono: "podemos usar
o reconhecimento"). Antes, com alvo, o rastreador pegava sempre a caixa mais
proxima da anterior: se o Joao saia do quadro e a Ana estava perto, o alvo
virava a Ana sem `person_lost` - e, se a Ana tivesse rosto conhecido, o FOLLOW
passava a seguir a Ana. Agora, quando o alvo tem nome:

- a caixa com o MESMO pessoa_id ganha, onde quer que esteja;
- caixa de OUTRA pessoa conhecida nunca vira o alvo;
- caixa sem rosto (de costas, longe) so continua o alvo se estiver colada a
  caixa anterior - e o alvo GUARDA o nome, em vez de perde-lo no quadro em que
  o rosto nao aparece (o FOLLOW so anda atras de gente reconhecida, e largava
  a pessoa toda vez que ela virava de costas);
- sem nenhuma dessas, conta como "ninguem" e o alvo se perde no timeout.
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


def _colada(caixa: Caixa, anterior: Caixa) -> bool:
    """A caixa nova e continuacao da anterior? Sobrepoe, ou o centro andou
    menos que uma largura da caixa anterior (a 2 quadros/s uma pessoa andando
    pode nao sobrepor, mas nao atravessa o quadro)."""
    if _iou(caixa, anterior) > 0:
        return True
    (ax, ay), (bx, by) = _centro(caixa), _centro(anterior)
    largura = max(1, anterior[2] - anterior[0])
    return (ax - bx) ** 2 + (ay - by) ** 2 <= largura**2


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

        if self._alvo is not None and self._alvo.pessoa_id is not None:
            return self._seguir_conhecido(caixas_candidatas, identidades, agora)

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

    def _seguir_conhecido(
        self,
        caixas: list[Caixa],
        identidades: dict[Caixa, tuple[int | None, str | None]],
        agora: float,
    ) -> Alvo | None:
        """Alvo com nome: segue o ROSTO, nao a proximidade. Ver docstring do modulo."""
        alvo = self._alvo
        mesma_pessoa = [c for c in caixas if identidades.get(c, (None, None))[0] == alvo.pessoa_id]
        if mesma_pessoa:
            escolhida = _caixa_mais_proxima(mesma_pessoa, alvo.caixa)
        else:
            sem_rosto_colada = [
                c
                for c in caixas
                if identidades.get(c, (None, None))[0] is None and _colada(c, alvo.caixa)
            ]
            if not sem_rosto_colada:
                # So ha estranhos longe ou OUTRA pessoa conhecida: para o
                # alvo, isso e ninguem. Perde no timeout, como sem deteccao.
                if agora - alvo.ultima_atualizacao > self._timeout_perdido_s:
                    self._alvo = None
                return self._alvo
            escolhida = _caixa_mais_proxima(sem_rosto_colada, alvo.caixa)
        self._alvo = Alvo(
            caixa=escolhida, pessoa_id=alvo.pessoa_id, nome=alvo.nome, ultima_atualizacao=agora
        )
        return self._alvo

    @property
    def alvo_atual(self) -> Alvo | None:
        return self._alvo

    def perdido(self, agora: float | None = None) -> bool:
        if self._alvo is None:
            return True
        agora = agora if agora is not None else time.monotonic()
        return (agora - self._alvo.ultima_atualizacao) > self._timeout_perdido_s
