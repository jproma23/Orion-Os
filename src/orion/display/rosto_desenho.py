"""Desenha o `Rosto` (rosto_estado.py) numa superficie pygame (2026-11-12).

Tela preta, dois olhos e uma boca no mesmo traco ciano, com um brilho suave em
volta. As expressoes mudam a FORMA dos olhos com "palpebras" pretas desenhadas
por cima (o truque dos robos de olhos de tela): feliz vira "^ ^", triste
inclina a palpebra para fora, sonolento achata. Tudo e proporcional ao tamanho
da tela - o mesmo codigo serve a janela de teste e o monitor do robo.

Burro de proposito: nao decide nada, so pinta o que o estado mandou.
"""
from __future__ import annotations

import math

import pygame

from orion.display.rosto_estado import (
    FALANDO,
    FELIZ,
    OUVINDO,
    PENSANDO,
    SONOLENTO,
    SUSTO,
    TRISTE,
    Rosto,
)

FUNDO = (0, 0, 0)
COR = (60, 220, 255)
#: Camadas do brilho: (quanto cresce, em fracao do tamanho; opacidade 0-255).
#: Muitas camadas finas = degrade suave; com 3 grossas apareciam faixas.
BRILHO = tuple((0.18 - i * 0.03, 12) for i in range(6))


def _brilho(tela: pygame.Surface, rect: pygame.Rect, raio: int) -> None:
    for cresce, alfa in BRILHO:
        folga = int(max(rect.w, rect.h) * cresce)
        maior = rect.inflate(folga * 2, folga * 2)
        camada = pygame.Surface(maior.size, pygame.SRCALPHA)
        pygame.draw.rect(
            camada, (*COR, alfa), camada.get_rect(), border_radius=raio + folga
        )
        tela.blit(camada, maior.topleft)


def _traco(tela: pygame.Surface, pontos: list[tuple[float, float]], raio: int) -> None:
    """Traco grosso de pontas redondas, com brilho - bolinhas sobrepostas ao
    longo dos pontos. Sai liso onde o `pygame.draw.arc` grosso deixava falhas."""
    if not pontos:
        return
    xs = [p[0] for p in pontos]
    ys = [p[1] for p in pontos]
    folga = int(raio * 3)
    caixa = pygame.Rect(
        int(min(xs)) - folga, int(min(ys)) - folga,
        int(max(xs) - min(xs)) + 2 * folga, int(max(ys) - min(ys)) + 2 * folga,
    )
    for cresce, alfa in BRILHO:
        camada = pygame.Surface(caixa.size, pygame.SRCALPHA)
        r = raio + int(raio * cresce * 9)
        for x, y in pontos:
            pygame.draw.circle(camada, (*COR, alfa), (int(x) - caixa.x, int(y) - caixa.y), r)
        tela.blit(camada, caixa.topleft)
    for x, y in pontos:
        pygame.draw.circle(tela, COR, (int(x), int(y)), raio)


def _arco(cx: float, cy: float, rx: float, ry: float, de: float, ate: float, n: int = 60):
    """Pontos de um arco de elipse (angulos em radianos; y da tela cresce para baixo)."""
    return [
        (cx + rx * math.cos(de + (ate - de) * i / n), cy - ry * math.sin(de + (ate - de) * i / n))
        for i in range(n + 1)
    ]


def _olho(tela: pygame.Surface, rosto: Rosto, cx: float, cy: float, lado: int) -> None:
    """`lado` = -1 olho esquerdo (da tela), +1 direito."""
    largura_tela, altura_tela = tela.get_size()
    escala = {SUSTO: 1.15, OUVINDO: 1.08}.get(rosto.expressao, 1.0)
    largura = int(largura_tela * 0.17 * escala)
    altura_cheia = int(altura_tela * 0.36 * escala)
    altura = max(int(altura_cheia * 0.06), int(altura_cheia * rosto.abertura_olhos))
    cx += rosto.olhar_x * largura_tela * 0.05
    cy += rosto.olhar_y * altura_tela * 0.05
    if rosto.expressao == SONOLENTO:
        cy += altura_cheia * 0.12  # olho pesado: achatado e um pouco caido
    rect = pygame.Rect(0, 0, largura, altura)
    rect.center = (int(cx), int(cy))

    if rosto.expressao == FELIZ:
        # "^ ^": o olho vira um ARCO - traco curvo de pontas redondas
        rx, ry = largura * 0.42, altura_cheia * 0.30
        pontos = _arco(cx, cy + altura_cheia * 0.10, rx, ry, math.radians(12), math.radians(168))
        _traco(tela, pontos, max(4, int(largura * 0.11)))
        return

    raio = int(min(largura, altura) * 0.42)
    _brilho(tela, rect, raio)
    pygame.draw.rect(tela, COR, rect, border_radius=raio)
    #: as palpebras cobrem tambem o brilho, senao sobra o contorno dele
    margem = int(max(largura, altura) * (BRILHO[0][0] + 0.02))

    # PALPEBRAS: preto por cima muda a forma sem mudar o olho.
    if rosto.expressao == TRISTE:
        # palpebra de cima caida para FORA do rosto: triangulo preto com o
        # canto de fora descendo ate quase metade do olho
        fora = rect.left - margem if lado < 0 else rect.right + margem
        dentro = rect.right + margem if lado < 0 else rect.left - margem
        topo = rect.top - margem
        pygame.draw.polygon(
            tela, FUNDO,
            [
                (fora, topo), (dentro, topo), (dentro, rect.top - 2),
                (fora, rect.top + int(altura * 0.5)),
            ],
        )
    elif rosto.expressao == PENSANDO:
        # uma sobrancelha levantada, so do lado para onde ele olha
        if lado > 0:
            traco = pygame.Rect(0, 0, int(largura * 0.9), max(4, int(altura_tela * 0.018)))
            traco.midbottom = (rect.centerx, rect.top - int(altura_tela * 0.05))
            _brilho(tela, traco, traco.h // 2)
            pygame.draw.rect(tela, COR, traco, border_radius=traco.h // 2)


def _boca(tela: pygame.Surface, rosto: Rosto) -> None:
    largura_tela, altura_tela = tela.get_size()
    cx, cy = largura_tela // 2, int(altura_tela * 0.80)
    espessura = max(4, int(altura_tela * 0.018))
    largura = int(largura_tela * 0.16)

    if rosto.expressao == FELIZ and rosto.abertura_boca < 0.05:
        # sorriso: arco de baixo (angulos negativos = metade de baixo)
        pontos = _arco(cx, cy - altura_tela * 0.03, largura * 0.5, altura_tela * 0.07,
                       math.radians(-160), math.radians(-20))
        _traco(tela, pontos, max(3, int(espessura * 0.75)))
        return
    if rosto.expressao == TRISTE:
        pontos = _arco(cx, cy + altura_tela * 0.05, largura * 0.38, altura_tela * 0.05,
                       math.radians(20), math.radians(160))
        _traco(tela, pontos, max(3, int(espessura * 0.6)))
        return
    if rosto.expressao == SUSTO:
        raio = int(altura_tela * 0.05)
        pygame.draw.circle(tela, COR, (cx, cy), raio, espessura)
        return

    abre = rosto.abertura_boca if rosto.expressao == FALANDO else 0.0
    altura = espessura + int(abre * altura_tela * 0.11)
    largura_atual = int(largura * (1.0 - 0.25 * abre))
    rect = pygame.Rect(0, 0, largura_atual, altura)
    rect.center = (cx, cy)
    raio = min(rect.w, rect.h) // 2
    _brilho(tela, rect, raio)
    pygame.draw.rect(tela, COR, rect, border_radius=raio)


def desenhar(tela: pygame.Surface, rosto: Rosto) -> None:
    largura, altura = tela.get_size()
    tela.fill(FUNDO)
    cy = altura * 0.42
    _olho(tela, rosto, largura / 2 - largura * 0.15, cy, -1)
    _olho(tela, rosto, largura / 2 + largura * 0.15, cy, +1)
    _boca(tela, rosto)
