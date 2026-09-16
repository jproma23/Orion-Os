"""Parser do LiDAR STL-50B2 (familia 3iRobotix Delta-2) - roda no Raspberry Pi
(Motion Core).

O protocolo foi descoberto por engenharia reversa na bancada em 2026-09-15
(medido no PC, COM4, adaptador CH340 - o Pi estava sem bootar naquele dia).

**Nao e LD06 e nao e COIN-D6.** O cabecalho `54 2C` do LD06 nao aparece em
nenhuma taxa, entao os drivers prontos (`ldlidar_stl_ros2`) nao servem. E o
COIN-D6 usa quadro de 36 bytes com cabecalho `55 AA 83 08` e exige comando de
start - este aqui e o contrario em tudo (85 bytes, `AA 55`, gira sozinho).

Formato do quadro (85 bytes, serial **230400** 8N1):

    +0..1    AA 55          cabecalho
    +2..3    00 19          numero de pontos = 25 (BIG endian)
    +4..5    angulo inicial (LITTLE endian, centesimos de grau)
    +6..7    angulo final   (LITTLE endian, centesimos de grau)
    +8..9    NAO DECIFRADO  (velocidade ou checksum - ver abaixo)
    +10..84  25 x 3 bytes   (intensidade, distancia_lo, distancia_hi)

A conta fecha: 85 - 10 = 75 = 25 x 3. O angulo caminha linearmente do inicial
ao final ao longo dos 25 pontos, cada quadro cobre ~27 graus, e sao ~13
quadros por volta (~325 pontos por volta). Distancia em MILIMETROS.

Validado ao vivo: duas voltas seguidas bateram dentro de poucos milimetros em
36 setores independentes (1495 vs 1491, 867 vs 859, 2183 vs 2187). E essa
repetibilidade que prova que a decodificacao esta certa - decodificacao errada
daria ruido diferente a cada volta.

Este modulo so DECODIFICA bytes; nao abre porta nenhuma. O acesso a serial e
responsabilidade de `motion_core/lidar/gerenciador.py`. Separar assim permite
testar o parser com bytes sinteticos sem hardware (mesma regra de
`tests/unit`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Prefixo fixo de todo quadro. O gerenciador usa isto para resincronizar.
HEADER_INICIO = b"\xaa\x55"
#: Comprimento de um quadro: 10 de cabecalho + 25 x 3 de pontos.
TAMANHO_QUADRO = 85
#: Pontos por quadro - fixado pelos bytes +2..3 (00 19 = 25, big endian).
PONTOS_POR_QUADRO = 25
#: Onde comecam os pontos dentro do quadro.
DESLOCAMENTO_PONTOS = 10
BYTES_POR_PONTO = 3

#: Taxa da serial. MEDIDA, nao suposta: em 115200 nao sai um byte sequer.
BAUD_PADRAO = 230400

#: O sensor manda o angulo em centesimos de grau (0..35999).
ESCALA_ANGULO = 100.0

#: Distancia 1 ou 2 mm NAO e objeto colado na lente: e o codigo de "nao
#: recebi eco". Sem este filtro a navegacao enxerga uma parede inexistente
#: encostada no robo e trava.
DISTANCIA_MINIMA_MM = 20
#: Limite de sanidade. Acima disso e quadro corrompido, nao medicao.
DISTANCIA_MAXIMA_MM = 12000

#: Este LiDAR NAO tem comando de start - ao energizar, o motor gira e os
#: dados fluem sozinhos (o anuncio dizia o contrario; foi medido). A ausencia
#: de `COMANDO_INICIAR` neste modulo e de proposito: o gerenciador procura o
#: atributo com getattr e simplesmente nao envia nada.


@dataclass(frozen=True, slots=True)
class PontoLidar:
    """Uma medicao: a que angulo, a que distancia, com que forca de retorno.

    `confianca` e a intensidade crua do sensor (0-255). O nome segue o que o
    gerenciador espera dos outros drivers, para que os tres sejam
    intercambiaveis.
    """

    angulo_graus: float
    distancia_mm: int
    confianca: int


@dataclass(frozen=True, slots=True)
class QuadroLidar:
    """Um pacote de 25 pontos cobrindo ~27 graus do giro."""

    angulo_inicial: float
    angulo_final: float
    pontos: tuple[PontoLidar, ...] = field(default_factory=tuple)
    #: Campo cru dos bytes +8..9, guardado sem interpretar (ver
    #: `velocidade_graus_s` abaixo para o porque).
    campo_nao_decifrado: int = 0

    @property
    def velocidade_graus_s(self) -> float:
        """Sempre 0 - a velocidade do motor deste sensor NAO foi decifrada.

        O gerenciador usa este campo so para calcular o `rpm` que publica em
        `lidar.scan`. Com 0 aqui, **o rpm publicado sai 0.0**: e um "nao sei",
        nao uma medicao de motor parado. Quem for usar o rpm para julgar
        saude do motor precisa saber disso - o sinal de LiDAR vivo neste
        modelo e a chegada de quadros validos, nao o rpm.

        Chutar um valor aqui seria pior: viraria numero inventado num campo
        que a navegacao le como medido.
        """
        return 0.0


def _u16_le(dados: bytes, i: int) -> int:
    """Le 2 bytes little-endian a partir de `i`."""
    return dados[i] | (dados[i + 1] << 8)


def _u16_be(dados: bytes, i: int) -> int:
    """Le 2 bytes big-endian a partir de `i`."""
    return (dados[i] << 8) | dados[i + 1]


def _interpolar_angulos(inicial: float, final: float, quantidade: int) -> list[float]:
    """Distribui `quantidade` angulos entre inicial e final.

    O cuidado aqui e a virada de 360 para 0: quando o quadro cruza o zero, o
    angulo final vem MENOR que o inicial. Somar 360 antes de interpolar evita
    que os pontos desse quadro sejam espalhados para tras pela volta inteira.
    """
    if quantidade <= 1:
        return [inicial]

    if final < inicial:
        final += 360.0

    passo = (final - inicial) / (quantidade - 1)
    return [(inicial + passo * i) % 360.0 for i in range(quantidade)]


def decodificar_quadro(quadro: bytes) -> QuadroLidar | None:
    """Decodifica UM quadro de 85 bytes. None se o quadro for invalido.

    Validar alem do cabecalho importa porque `AA 55` aparece por acaso no meio
    dos dados de distancia (sao bytes binarios puros). Como o campo +8..9 nao
    foi decifrado, **nao ha checksum para conferir** - a defesa contra quadro
    espurio e a contagem de pontos e a faixa de angulo.
    """
    if quadro is None or len(quadro) < TAMANHO_QUADRO:
        return None
    if quadro[0:2] != HEADER_INICIO:
        return None
    if _u16_be(quadro, 2) != PONTOS_POR_QUADRO:
        return None

    angulo_inicial = _u16_le(quadro, 4) / ESCALA_ANGULO
    angulo_final = _u16_le(quadro, 6) / ESCALA_ANGULO

    # Angulo fora de 0..360 depois da escala significa quadro desalinhado.
    if not (0.0 <= angulo_inicial < 360.0 and 0.0 <= angulo_final < 360.0):
        return None

    angulos = _interpolar_angulos(angulo_inicial, angulo_final, PONTOS_POR_QUADRO)

    pontos = []
    for i in range(PONTOS_POR_QUADRO):
        base = DESLOCAMENTO_PONTOS + i * BYTES_POR_PONTO
        pontos.append(
            PontoLidar(
                angulo_graus=angulos[i],
                distancia_mm=_u16_le(quadro, base + 1),
                confianca=quadro[base],
            )
        )

    return QuadroLidar(
        angulo_inicial=angulo_inicial,
        angulo_final=angulo_final,
        pontos=tuple(pontos),
        campo_nao_decifrado=_u16_le(quadro, 8),
    )


def pontos_validos(quadro: QuadroLidar) -> tuple[PontoLidar, ...]:
    """So os pontos que sao medicao de verdade (ver DISTANCIA_MINIMA_MM)."""
    return tuple(
        p
        for p in quadro.pontos
        if DISTANCIA_MINIMA_MM <= p.distancia_mm <= DISTANCIA_MAXIMA_MM
    )


def acumular_scan(quadros: list[QuadroLidar]) -> dict[int, PontoLidar]:
    """Junta varios quadros numa varredura polar: angulo(graus) -> ponto.

    Quando o mesmo angulo chega duas vezes (quadros sobrepostos, ou mais de
    uma volta na janela), fica o ponto de MENOR distancia - um obstaculo perto
    vale mais que um reflexo distante no mesmo angulo.

    Os pontos "sem retorno" (1-2 mm) sao descartados aqui e nao no
    gerenciador de proposito: nao receber eco e semantica do PROTOCOLO, nao
    politica de navegacao. Deixar passar faria o menor-vence acima escolher
    sempre o 1 mm e transformar cada direcao sem eco em parede colada.
    """
    scan: dict[int, PontoLidar] = {}
    for quadro in quadros:
        for ponto in pontos_validos(quadro):
            angulo_int = int(ponto.angulo_graus) % 360
            atual = scan.get(angulo_int)
            if atual is None or ponto.distancia_mm < atual.distancia_mm:
                scan[angulo_int] = ponto
    return scan
