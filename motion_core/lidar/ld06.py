"""Parser do LiDAR LD06 (LDROBOT) - roda no Raspberry Pi (Motion Core).

O LD06 e um sensor ToF de 360 graus que fala UART serial unidirecional a
230400 baud (8N1), ligado ao Pi via conversor USB-UART (decisao do dono em
2026-08-17). Cada quadro tem 47 bytes e carrega 12 pontos; a 10-15 Hz de
rotacao sao ~30 quadros por varredura completa.

Formato do quadro (todos os campos little-endian, ver manual LDROBOT):

    [0x54] [0x2C] [speed 2B] [start_angle 2B] [12 x 3B de ponto]
    [end_angle 2B] [timestamp 2B] [crc8 1B]

    - 0x54: inicio fixo.
    - 0x2C: os 5 bits baixos = quantidade de pontos (hoje 12).
    - speed: rotacao em graus por segundo.
    - start/end angle: em centesimos de grau (0.01 graus por unidade).
    - ponto: [dist_low][dist_high][confianca], distancia em MILIMETROS.
    - timestamp: milissegundos desde o boot do LiDAR (reinicia aos 30000).
    - crc8: CRC8 polinomio 0x4D, init 0x00, sem reflexao, sem xorout, sobre
      todos os bytes anteriores (0..45).

O angulo de cada um dos 12 pontos e interpolado linearmente entre o angulo
inicial e o final do quadro (a resolucao angular real e o passo entre pontos
consecutivos).

Este modulo so PARSEIA bytes; nao abre porta nenhuma. O acesso a serial e
responsabilidade de `motion_core/lidar/gerenciador.py`. Separar assim permite
testar o parser com bytes sinteticos sem hardware (mesma regra de
`tests/unit`).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

#: Primeiro byte de todo quadro.
HEADER = 0x54
#: Comprimento maximo de um quadro: 1 + 1 + 2 + 2 + (12 * 3) + 2 + 2 + 1.
TAMANHO_QUADRO = 47
#: Pontos por quadro - fixado pelos 5 bits baixos de `ver_len` (0x2C).
PONTOS_POR_QUADRO = 12

#: Formato do struct (ver gibbard.me / manual LDROBOT): B header, B ver_len,
#: H speed, H start_angle, 12 x (H distancia, B confianca), H end_angle,
#: H timestamp, B crc.
_FORMATO = "<BBHH" + "HB" * PONTOS_POR_QUADRO + "HHB"


@dataclass
class PontoLidar:
    """Um ponto da varredura, em coordenadas polares no referencial do LiDAR.

    Angulo em graus [0, 360), distancia em milimetros, confianca 0-255.
    """

    angulo_graus: float
    distancia_mm: int
    confianca: int


@dataclass
class QuadroLidar:
    """Um quadro decodificado, ainda sem os 360 graus montados."""

    velocidade_graus_s: int
    pontos: list[PontoLidar] = field(default_factory=list)
    #: Soma dos angulos inicial e final (centesimos de grau) para quem quiser
    #: conferir a interpolacao.
    start_angle_raw: int = 0
    end_angle_raw: int = 0


def crc8_ld06(dados: bytes) -> int:
    """CRC8 do LD06: polinomio 0x4D, init 0x00, sem reflexao, sem xorout.

    Implementado por tabela (256 entradas) - e a mesma tabela que vem no
    manual oficial.
    """
    crc = 0
    for byte in dados:
        crc = _CRC_TABLE[crc ^ byte]
    return crc


def _gerar_tabela_crc8(poligono: int = 0x4D) -> list[int]:
    tabela: list[int] = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ poligono) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
        tabela.append(crc)
    return tabela


_CRC_TABLE = _gerar_tabela_crc8()


def decodificar_quadro(quadro: bytes) -> QuadroLidar | None:
    """Decodifica UM quadro de 47 bytes. None se o quadro for invalido.

    Validacoes: tamanho, header, contagem de pontos e CRC8. Nao valida a
    faixa de angulo/distancia - quem consome decide o que fazer com pontos
    fora da faixa util.
    """
    if quadro is None or len(quadro) != TAMANHO_QUADRO:
        return None
    if quadro[0] != HEADER:
        return None

    # CRC sobre os bytes 0..45; o ultimo byte e o proprio CRC.
    if crc8_ld06(quadro[:46]) != quadro[46]:
        return None

    desempacotado = struct.unpack(_FORMATO, quadro)
    # desempacotado[0] = header, [1] = ver_len (0x2C), [2] = speed,
    # [3] = start_angle, [4..27] = 12 pares (dist, confianca) como 24 ints,
    # [28] = end_angle, [29] = timestamp, [30] = crc.
    ver_len = desempacotado[1]
    pontos_count = ver_len & 0x1F
    if pontos_count != PONTOS_POR_QUADRO:
        return None

    velocidade_graus_s = desempacotado[2]
    start_angle_raw = desempacotado[3]
    end_angle_raw = desempacotado[28]

    # Interpolacao linear do angulo de cada ponto (0.01 graus por unidade).
    # Trata o wrap de 360 graus (end menor que start = cruzou o zero).
    start = float(start_angle_raw)
    end = float(end_angle_raw)
    if end < start:
        end += 36000.0
    passo = (end - start) / PONTOS_POR_QUADRO

    pontos: list[PontoLidar] = []
    for i in range(PONTOS_POR_QUADRO):
        distancia = desempacotado[4 + i * 2]
        confianca = desempacotado[5 + i * 2]
        angulo_raw = start + passo * i
        angulo = (angulo_raw % 36000.0) / 100.0
        pontos.append(PontoLidar(angulo_graus=angulo, distancia_mm=distancia, confianca=confianca))

    return QuadroLidar(
        velocidade_graus_s=velocidade_graus_s,
        pontos=pontos,
        start_angle_raw=start_angle_raw,
        end_angle_raw=end_angle_raw,
    )


def acumular_scan(quadros: list[QuadroLidar]) -> dict[int, PontoLidar]:
    """Junta varios quadros numa varredura polar: angulo(graus) -> ponto.

    Quando o mesmo angulo chega duas vezes (quadros sobrepostos), fica o
    ponto de MENOR distancia - um obstaculo perto vale mais que um reflexo
    distante no mesmo angulo.
    """
    scan: dict[int, PontoLidar] = {}
    for quadro in quadros:
        for ponto in quadro.pontos:
            angulo_int = int(ponto.angulo_graus) % 360
            atual = scan.get(angulo_int)
            if atual is None or ponto.distancia_mm < atual.distancia_mm:
                scan[angulo_int] = ponto
    return scan
