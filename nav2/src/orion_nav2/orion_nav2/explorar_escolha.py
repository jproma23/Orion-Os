"""Para onde explorar: CURIOSIDADE por ganho de informacao (2026-11-14).

Pedido do dono: *"faz ele entrar no corredor desvendar novos ambientes
curiosidade"*. Ate aqui o explorador ia na fronteira mais LONGE (o
"esqueleto da casa", pedido de 31/10). Distancia nao e curiosidade: uma
fronteira longe pode ser o canto do mesmo comodo, e a porta do corredor, a
1 m, abre uma sala inteira que ele nunca viu.

GANHO = quanto DESCONHECIDO ha atras da fronteira, contado andando so por
celulas desconhecidas a partir dela, ate `RAIO_GANHO_M`. Andar pelo
desconhecido (e nao contar um disco) e o que impede parede de enganar: atras
de uma parede (celulas ocupadas) a contagem para. Uma porta ou corredor que
da numa sala inteira tem ganho grande; o canto do comodo atual, pequeno.

NOTA = ganho x (bonus da distancia). A distancia ainda pesa a favor - a regra
de 31/10 ("as maiores distancias primeiro") vira desempate, nao criterio.

Puro (so numpy), para ser testado sem ROS: o explorar.py, dentro do
container do Nav2, so traduz o /map para cá.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

LIVRE = 0
DESCONHECIDO = -1

MIN_CELULAS_FRONTEIRA = 6       # 6 x 5 cm: menos que isso e ruido do LiDAR
DISTANCIA_MINIMA_M = 0.5        # alvo mais perto que isso nao leva a lugar novo
RAIO_LISTA_NEGRA_M = 0.5        # alvo que ja falhou: nao tentar perto dele de novo
RAIO_GANHO_M = 1.5              # ate onde contar o desconhecido atras da fronteira
#: Bonus da distancia: 1,0 colado, ate 1,5 a 3 m ou mais. Desempate, nao regra.
DISTANCIA_BONUS_M = 3.0
BONUS_MAXIMO = 0.5
#: Ganho minimo (m2) para valer a viagem: menos que isto e fresta.
GANHO_MINIMO_M2 = 0.05


def grupos_de_fronteira(
    grade: np.ndarray, resolucao: float, ox: float, oy: float
) -> list[tuple[float, float, int, list[tuple[int, int]]]]:
    """(x, y, tamanho, celulas) de cada grupo de fronteira.

    Fronteira = celula LIVRE encostada (4-vizinhos) em DESCONHECIDA. O ponto
    do grupo e a celula mais perto do centro do grupo - um ponto que existe
    e e livre, nao o centroide (que num grupo em "L" cai na parede).
    """
    alt, larg = grade.shape
    livre = grade == LIVRE
    desconhecido = grade == DESCONHECIDO
    viz = np.zeros_like(livre)
    viz[1:, :] |= desconhecido[:-1, :]
    viz[:-1, :] |= desconhecido[1:, :]
    viz[:, 1:] |= desconhecido[:, :-1]
    viz[:, :-1] |= desconhecido[:, 1:]
    fronteira = livre & viz
    visto = np.zeros_like(fronteira)
    grupos = []
    for i, j in zip(*np.nonzero(fronteira), strict=True):
        if visto[i, j]:
            continue
        fila, celulas = deque([(i, j)]), []
        visto[i, j] = True
        while fila:
            a, b = fila.popleft()
            celulas.append((a, b))
            for da in (-1, 0, 1):
                for db in (-1, 0, 1):
                    na, nb = a + da, b + db
                    if 0 <= na < alt and 0 <= nb < larg and fronteira[na, nb] and not visto[na, nb]:
                        visto[na, nb] = True
                        fila.append((na, nb))
        if len(celulas) < MIN_CELULAS_FRONTEIRA:
            continue
        ci = sum(c[0] for c in celulas) / len(celulas)
        cj = sum(c[1] for c in celulas) / len(celulas)
        a, b = min(celulas, key=lambda c: (c[0] - ci) ** 2 + (c[1] - cj) ** 2)
        x, y = ox + (b + 0.5) * resolucao, oy + (a + 0.5) * resolucao
        grupos.append((x, y, len(celulas), celulas))
    return grupos


def ganho_m2(grade: np.ndarray, resolucao: float, celulas: list[tuple[int, int]]) -> float:
    """Area desconhecida (m2) alcancavel a partir da fronteira SO pelo desconhecido."""
    alt, larg = grade.shape
    raio = max(1, int(RAIO_GANHO_M / resolucao))
    ci = sum(c[0] for c in celulas) / len(celulas)
    cj = sum(c[1] for c in celulas) / len(celulas)
    visto = np.zeros(grade.shape, dtype=bool)
    fila: deque[tuple[int, int]] = deque()
    for a, b in celulas:
        for na, nb in ((a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)):
            dentro = 0 <= na < alt and 0 <= nb < larg
            if dentro and grade[na, nb] == DESCONHECIDO and not visto[na, nb]:
                visto[na, nb] = True
                fila.append((na, nb))
    contagem = 0
    while fila:
        a, b = fila.popleft()
        contagem += 1
        for na, nb in ((a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)):
            if not (0 <= na < alt and 0 <= nb < larg) or visto[na, nb]:
                continue
            if grade[na, nb] != DESCONHECIDO:
                continue
            if (na - ci) ** 2 + (nb - cj) ** 2 > raio * raio:
                continue
            visto[na, nb] = True
            fila.append((na, nb))
    return contagem * resolucao * resolucao


def escolher(
    grade: np.ndarray,
    resolucao: float,
    ox: float,
    oy: float,
    pose: tuple[float, float],
    lista_negra: list[tuple[float, float]],
) -> dict | None:
    """O alvo mais CURIOSO, ou None (nada mais a descobrir que valha a viagem)."""
    candidatos = []
    for x, y, n, celulas in grupos_de_fronteira(grade, resolucao, ox, oy):
        d = math.hypot(x - pose[0], y - pose[1])
        if d < DISTANCIA_MINIMA_M:
            continue
        if any(math.hypot(x - bx, y - by) < RAIO_LISTA_NEGRA_M for bx, by in lista_negra):
            continue
        ganho = ganho_m2(grade, resolucao, celulas)
        if ganho < GANHO_MINIMO_M2:
            continue
        bonus = 1.0 + BONUS_MAXIMO * min(d, DISTANCIA_BONUS_M) / DISTANCIA_BONUS_M
        candidatos.append({"x": x, "y": y, "celulas": n, "distancia": d,
                           "ganho_m2": ganho, "nota": ganho * bonus})
    if not candidatos:
        return None
    melhor = max(candidatos, key=lambda c: c["nota"])
    melhor["grupos"] = len(candidatos)
    return melhor
