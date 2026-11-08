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


#: Resolucao da busca da passagem, em graus.
PASSO_BUSCA_GRAUS = 10
#: Ate esta largura um arco livre e PASSAGEM (porta, vao) e ele mira o meio;
#: mais largo e sala aberta, e ele faz o menor giro com folga da borda.
LARGURA_MAX_PASSAGEM_GRAUS = 90
MARGEM_BORDA_GRAUS = 20


def _centro_da_passagem(
    raios_cm, passo_inteiro_cm: float, meia_largura_cm: float
) -> tuple[int, float, int] | None:
    """(desvio ate o meio da passagem, livre la, largura em graus) ou None.

    Desvio com sinal (positivo = direita), de -170 a +180. Uma passagem e um
    arco de direcoes vizinhas em que cabe um passo inteiro pelo corredor do
    corpo; os arcos dao a volta (o de -170 encosta no de +180). Entre varias,
    vale a que pede o menor giro; no empate, a mais larga.
    """
    desvios = list(range(-180 + PASSO_BUSCA_GRAUS, 181, PASSO_BUSCA_GRAUS))
    livres = [
        livre_na_direcao(raios_cm, (90.0 + d) % 360.0, meia_largura_cm) for d in desvios
    ]
    cabe = [lv is not None and lv >= passo_inteiro_cm for lv in livres]
    if not any(cabe):
        return None
    n = len(desvios)
    if all(cabe):
        return None  # tudo aberto: a frente ja teria passado; nada a centrar
    # Comeca a varrer logo depois de uma direcao que NAO cabe, para nenhum
    # arco ficar partido na emenda -180/+180.
    inicio = next(i for i in range(n) if not cabe[i]) + 1
    arcos: list[list[int]] = []
    atual: list[int] = []
    for k in range(n):
        i = (inicio + k) % n
        if cabe[i]:
            atual.append(i)
        elif atual:
            arcos.append(atual)
            atual = []
    if atual:
        arcos.append(atual)

    melhor: tuple[int, int, int] | None = None  # (|giro|, -largura, indice)
    for arco in arcos:
        if len(arco) * PASSO_BUSCA_GRAUS > LARGURA_MAX_PASSAGEM_GRAUS:
            # Nao e porta, e sala aberta: o "meio" de 290 graus livres fica
            # nas costas. Vale o menor giro, com folga da borda (o que esta na
            # borda e o obstaculo que ele acabou de ver).
            folga = MARGEM_BORDA_GRAUS // PASSO_BUSCA_GRAUS
            miolo = arco[folga:-folga] or arco
            alvo = min(miolo, key=lambda i: abs(desvios[i]))
        else:
            alvo = arco[len(arco) // 2] if len(arco) % 2 else None
            if alvo is None:
                # numero par de direcoes: o meio fica entre duas; usa a de menor giro
                a, b = arco[len(arco) // 2 - 1], arco[len(arco) // 2]
                alvo = a if abs(desvios[a]) <= abs(desvios[b]) else b
        meio = alvo
        chave = (abs(desvios[meio]), -len(arco), meio)
        if melhor is None or chave < melhor:
            melhor = chave
    _, menos_largura, indice = melhor
    return desvios[indice], livres[indice], -menos_largura * PASSO_BUSCA_GRAUS


# --- ALINHAMENTO PELAS PAREDES (2026-11-08) -----------------------------------
#
# Antes de avancar, o robo confere as paredes dos lados e corrige o rumo:
# PARALELO a parede mais proxima e, num corredor (parede dos dois lados), para
# o MEIO dele. O dono pediu "auto alinhamento" - andar torto ao longo de uma
# parede e o que faz o corredor de 22 cm encostar num lado.
#
# A conta e geometrica, em coordenadas do robo: y para a FRENTE, x para a
# ESQUERDA (raio no angulo a, convencao do projeto 0 esquerda / 90 frente:
# x = r cos a, y = r sin a). Os pontos de uma parede lateral reta ficam numa
# reta x = m*y + c: m = 0 e paralelo, `c` e a distancia ate ela. O giro que
# deixa paralelo e -atan(m) (positivo = direita): parede que se afasta a
# frente (m > 0 na esquerda) quer dizer que o robo aponta para a direita dela.

#: So conta como parede o que esta a menos disto do lado.
PAREDE_MAX_CM = 100.0
#: Raios usados para achar cada parede: ate este angulo do perpendicular.
PAREDE_JANELA_GRAUS = 50
#: Uma reta so e parede com pontos suficientes, espalhados ao longo da frente,
#: e bem alinhados (rms do residuo).
PAREDE_MIN_PONTOS = 8
PAREDE_MIN_EXTENSAO_CM = 30.0
PAREDE_MAX_RESIDUO_CM = 3.0
#: Distancia a frente usada para converter o desvio do meio do corredor em
#: angulo: chegar ao meio em ~1 m.
CENTRALIZAR_EM_CM = 100.0
#: Abaixo disto nao vale girar (ruido); acima, nao e alinhamento, e manobra.
AJUSTE_MIN_GRAUS = 5.0
AJUSTE_MAX_GRAUS = 30.0


def _parede(raios_cm, perpendicular_graus: float) -> tuple[float, float] | None:
    """(m, c) da reta x = m*y + c da parede daquele lado, ou None."""
    xs: list[float] = []
    ys: list[float] = []
    for delta in range(-PAREDE_JANELA_GRAUS, PAREDE_JANELA_GRAUS + 1):
        angulo = int(round(perpendicular_graus + delta)) % 360
        r = raios_cm[angulo] if angulo < len(raios_cm) else None
        if r is None:
            continue
        x = r * math.cos(math.radians(angulo))
        y = r * math.sin(math.radians(angulo))
        if abs(x) > PAREDE_MAX_CM:
            continue
        xs.append(x)
        ys.append(y)
    if len(xs) < PAREDE_MIN_PONTOS or max(ys) - min(ys) < PAREDE_MIN_EXTENSAO_CM:
        return None
    n = len(xs)
    my, mx = sum(ys) / n, sum(xs) / n
    syy = sum((y - my) ** 2 for y in ys)
    if syy == 0:
        return None
    m = sum((y - my) * (x - mx) for x, y in zip(xs, ys, strict=True)) / syy
    c = mx - m * my
    residuo = math.sqrt(sum((x - (m * y + c)) ** 2 for x, y in zip(xs, ys, strict=True)) / n)
    if residuo > PAREDE_MAX_RESIDUO_CM:
        return None
    return m, c


def ajuste_de_rumo(raios_cm) -> tuple[float, str] | None:
    """Giro (graus, positivo = direita) que alinha o robo antes de avancar.

    None = nada a corrigir: sem parede reta perto, ou o ajuste e pequeno
    demais (ruido) ou grande demais (nao e alinhar, e outra manobra - deixa
    para o desvio). O texto vai para o log.
    """
    if not raios_cm:
        return None
    esquerda = _parede(raios_cm, 0.0)
    direita = _parede(raios_cm, 180.0)
    if esquerda is None and direita is None:
        return None
    if esquerda is not None and direita is not None:
        # Corredor: paralelo pela media das duas, e o meio pelos dois `c`.
        m = (esquerda[0] + direita[0]) / 2.0
        meio_cm = (esquerda[1] + direita[1]) / 2.0  # > 0: o meio esta a esquerda
        giro = -math.degrees(math.atan(m)) - math.degrees(
            math.atan(meio_cm / CENTRALIZAR_EM_CM)
        )
        motivo = (
            f"corredor: esquerda {esquerda[1]:.0f} cm, direita {-direita[1]:.0f} cm, "
            f"torto {-math.degrees(math.atan(m)):+.0f} graus"
        )
    else:
        lado, (m, c) = ("esquerda", esquerda) if esquerda is not None else ("direita", direita)
        giro = -math.degrees(math.atan(m))
        motivo = f"parede a {lado} a {abs(c):.0f} cm, torto {giro:+.0f} graus"
    if abs(giro) < AJUSTE_MIN_GRAUS or abs(giro) > AJUSTE_MAX_GRAUS:
        return None
    return round(giro, 1), motivo


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

    passo_inteiro = limite_cm + passo_max_cm

    # PARA O MEIO DA PASSAGEM (2026-11-08, dono: "auto alinhamento"). Antes
    # ele girava para o PRIMEIRO desvio de 30 em 30 graus que coubesse - e
    # numa porta isso e a borda dela, nao o meio: a proxima varredura achava
    # o batente na frente e girava de novo. Agora mede de 10 em 10 graus,
    # junta as direcoes vizinhas que comportam um passo inteiro em ARCOS
    # (cada arco e uma passagem) e aponta para o CENTRO do arco que pede o
    # menor giro.
    passagem = _centro_da_passagem(raios_cm, passo_inteiro, meia_largura_cm)
    if passagem is not None:
        desvio, livre, largura = passagem
        lado = "direita" if desvio > 0 else "esquerda"
        return Passo(
            GIRAR,
            float(desvio),
            livre,
            f"frente curta ({'sem medida' if frente is None else f'{frente:.0f} cm'}); "
            f"passagem de {largura} graus, o meio esta {abs(desvio)} graus a {lado} "
            f"com {livre:.0f} cm",
        )

    # Nenhuma passagem comporta um passo inteiro: vale a direcao mais aberta
    # que comporte ao menos o passo minimo, no menor giro.
    melhor: tuple[float, int] | None = None  # (livre, desvio)
    for desvio in DESVIOS_GRAUS:
        livre = livre_na_direcao(raios_cm, (90.0 + desvio) % 360.0, meia_largura_cm)
        if livre is None or livre < precisa:
            continue
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
