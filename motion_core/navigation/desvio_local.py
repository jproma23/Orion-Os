"""Desvio local: o EXPLORE escolhe o proximo passo olhando o LiDAR (2026-11-01).

POR QUE EXISTE. Em 01/11 o robo bateu duas vezes (comoda e parede) em modo
autonomo. O dono: *"o problema nao esta em decidir andar mas em nao desviar os
obstaculos"*. O EXPLORE andava reto em pedacos fixos de 30 cm; quando algo
aparecia, so encerrava o trecho e devolvia a decisao a IA - que mandava
"explorar" de novo, reto, para o mesmo lugar. As contas para desviar existiam
(`livre_no_corredor`, em estado_do_mundo.py), mas so iam para o texto da IA:
ninguem que mexe nas rodas as usava.

O QUE FAZ: dado o LiDAR (360 raios, convencao do projeto: 0 esquerda, 90
frente, 180 direita), decide UM passo sem perguntar a ninguem:

- frente com espaco -> AVANCAR o que cabe: `min(passo_max, livre - limite)`;
- frente curta -> GIRAR para a direcao mais aberta (no empate, o menor giro);
- nenhuma direcao com espaco -> BLOQUEADO (ai sim a IA e avisada).

"Espaco" e medido no CORREDOR da largura do corpo, nao num cone: parede ao
lado de um corredor nao conta, caixa na frente conta. E a mesma conta da
trava do LiDAR, com a mesma meia-largura, para o desvio nunca escolher um
passo que a trava recusaria.

NAO SEI NAO E LIVRE: uma direcao sem ponto nenhum no corredor so conta como
livre se o LiDAR mediu de verdade em volta dela (raios com eco na janela de
`JANELA_COBERTURA_GRAUS`). Sem eco ali pode ser vidro, ou algo perto demais
(abaixo do `min_distancia_mm` do LiDAR) - e e para la que ele nao vai.

Funcao pura, sem bus nem hardware: testavel com listas de raios sinteticas.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

AVANCAR = "avancar"
GIRAR = "girar"
BLOQUEADO = "bloqueado"

#: Desvios testados, em graus a partir da frente (positivo = direita, como o
#: TURN_RIGHT do `_executar_segmento`). A ordem desempata: menor giro primeiro.
DESVIOS_GRAUS = (30, -30, 60, -60, 90, -90, 120, -120, 180)

#: FRENTE CEGA E OBSTACULO (2026-11-01, medido). Com uma parede a ~30 cm e de
#: frente, o STL-50B2 devolve "sem eco" em 3 de cada 4 varreduras nos raios
#: perpendiculares a ela (bruto: 30 s parado, 31 de 118 varreduras com a parede
#: a 29-31 cm; o resto, nada). Nao ha peca do robo na frente (nenhum ponto a
#: menos de 12 cm em 360 graus). Ou seja: o sensor some com o obstaculo
#: justamente quando ele esta perto e na frente. Por isso, na janela de
#: `JANELA_CEGA_GRAUS` em volta do eixo do AVANCO, raios sem eco demais =
#: obstaculo (fail-closed). Giro e re seguem livres.
JANELA_CEGA_GRAUS = 20
FRACAO_CEGA_MAXIMA = 0.6


def fracao_sem_eco(raios_cm, eixo_graus: float, janela_graus: int = JANELA_CEGA_GRAUS) -> float:
    """Fracao dos raios sem eco na janela de +-janela_graus em volta do eixo."""
    total = 0
    sem_eco = 0
    for delta in range(-janela_graus, janela_graus + 1):
        indice = int(round(eixo_graus + delta)) % 360
        total += 1
        if not raios_cm or indice >= len(raios_cm) or raios_cm[indice] is None:
            sem_eco += 1
    return sem_eco / total


#: Quanto vale "livre ate onde mede" quando o corredor nao tem ponto nenhum
#: mas a direcao foi medida - o alcance util dentro de casa.
LIVRE_SEM_PONTO_CM = 300.0
#: Janela em volta do eixo onde precisa haver eco para "sem ponto" valer como
#: livre, e quantos raios com eco ela exige.
JANELA_COBERTURA_GRAUS = 20
RAIOS_MINIMOS_COBERTURA = 10


@dataclass(frozen=True)
class Passo:
    """O que fazer agora. `valor` e cm (AVANCAR), graus com sinal (GIRAR,
    positivo = direita) ou None (BLOQUEADO). `livre_cm` e o que foi medido na
    direcao escolhida - vai para o log."""

    acao: str
    valor: float | None
    livre_cm: float | None
    motivo: str


def livre_na_direcao(raios_cm, eixo_graus: float, meia_largura_cm: float) -> float | None:
    """Quanto da para andar no eixo antes de um ponto entrar no corredor.

    None = NAO SEI (sem ponto no corredor e sem eco suficiente em volta, ou
    com a janela em volta do eixo cega demais - ver FRACAO_CEGA_MAXIMA).
    """
    if fracao_sem_eco(raios_cm, eixo_graus) > FRACAO_CEGA_MAXIMA:
        return None
    menor: float | None = None
    for angulo, distancia in enumerate(raios_cm or ()):
        if distancia is None:
            continue
        desvio = math.radians(angulo - eixo_graus)
        avanco = distancia * math.cos(desvio)
        if avanco > 0 and abs(distancia * math.sin(desvio)) < meia_largura_cm:
            if menor is None or avanco < menor:
                menor = avanco
    if menor is not None:
        return menor
    com_eco = 0
    for delta in range(-JANELA_COBERTURA_GRAUS, JANELA_COBERTURA_GRAUS + 1):
        indice = int(round(eixo_graus + delta)) % 360
        if raios_cm and indice < len(raios_cm) and raios_cm[indice] is not None:
            com_eco += 1
    return LIVRE_SEM_PONTO_CM if com_eco >= RAIOS_MINIMOS_COBERTURA else None


def escolher_passo(
    raios_cm,
    limite_cm: float = 50.0,
    passo_max_cm: float = 30.0,
    passo_min_cm: float = 10.0,
    meia_largura_cm: float = 25.0,
) -> Passo:
    """Decide o proximo passo do EXPLORE. Ver a docstring do modulo."""
    if not raios_cm:
        return Passo(BLOQUEADO, None, None, "sem leitura do LiDAR")

    precisa = limite_cm + passo_min_cm
    frente = livre_na_direcao(raios_cm, 90.0, meia_largura_cm)
    if frente is not None and frente >= precisa:
        andar = min(passo_max_cm, frente - limite_cm)
        return Passo(AVANCAR, round(andar, 1), frente, "frente com espaco")

    # Prefere o MENOR giro que ja permite um passo inteiro - explorar e seguir
    # em frente o mais possivel, nao dar meia-volta porque atras tem 10 cm a
    # mais. So quando nenhuma direcao comporta um passo inteiro vale a mais
    # aberta. DESVIOS_GRAUS ja esta em ordem de giro crescente.
    passo_inteiro = limite_cm + passo_max_cm
    melhor: tuple[float, int] | None = None  # (livre, desvio)
    for desvio in DESVIOS_GRAUS:
        livre = livre_na_direcao(raios_cm, (90.0 + desvio) % 360.0, meia_largura_cm)
        if livre is None or livre < precisa:
            continue
        if livre >= passo_inteiro:
            melhor = (livre, desvio)
            break
        if melhor is None or livre > melhor[0]:
            melhor = (livre, desvio)

    if melhor is None:
        return Passo(
            BLOQUEADO,
            None,
            frente,
            f"nenhuma direcao com {precisa:.0f} cm livres",
        )
    livre, desvio = melhor
    lado = "direita" if desvio > 0 else "esquerda"
    return Passo(
        GIRAR,
        float(desvio),
        livre,
        f"frente curta ({'sem medida' if frente is None else f'{frente:.0f} cm'}); "
        f"{abs(desvio)} graus a {lado} tem {livre:.0f} cm",
    )
