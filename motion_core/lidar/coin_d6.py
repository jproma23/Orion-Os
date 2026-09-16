"""Parser do LiDAR COIN-D6 (Gouke Optronics / WitMotion) - roda no Raspberry Pi
(Motion Core).

O COIN-D6 e um ToF dToF de 360 graus que fala UART serial bidirecional a
230400 baud (8N1), ligado ao Pi via USB CH340. Diferente do LD06 (passivo),
ele so emite dados DEPOIS de receber o comando de start `AA 55 F0 0F`.

Cada quadro tem 36 bytes e carrega 8 pontos; a ~400 pontos por volta (0,9 grau
de resolucao) sao ~50 quadros por varredura a 10 Hz. Formato do quadro
(ver struct `node_package_coin` do SDK oficial, versao M1CT_Coin_Plus):

    [0x55] [0xAA] [0x83] [0x08] [speed 2B] [start_angle 2B]
    [8 x 3B de ponto: d0=dist_low d1=dist_high d2=qualidade]
    [stop_angle 2B] [checksum 2B]

    - 0x55 0xAA 0x83 0x08: header fixo; 0x08 = 8 pontos por quadro.
    - speed: campo do quadro; o SDK nao o usa (a frequencia real vem de um
      pacote de sincronizacao separado). Mantido apenas como referencia.
    - start/stop angle: codigo de angulo em unidades de 1/64 de grau, com
      base 0xA000 (FRAME_ANGLE_BASE) e faixa de 90*256 unidades por volta
      (FRAME_ANGLE_RANGE). angulo_graus = (codigo - 0xA000) * 360 / (90*256)
      = (codigo - 0xA000) / 64.
    - ponto: distancia_q2 = (d0 + d1*256) & 0x3FFF em MILIMETROS (o driver
      ROS publica range = distance_q2/1000.0f); sync_quality = d2 >> 2
      (6 bits).
    - checksum: sobre os primeiros 34 bytes lidos como 17 words de 16 bits
      (little-endian): chk32 = ((chk32 << 1) + word) & 0xFFFFFFFF (int de 32
      bits com estouro), depois dobra os 15 bits altos nos baixos:
      chk = (chk32 & 0x7FFF) + (chk32 >> 15); chk &= 0x7FFF. Confere com
      csL/csH no fim do quadro.

O angulo de cada um dos 8 pontos e interpolado linearmente entre o angulo
inicial e o final do quadro (resolucao angular real = passo entre pontos).

Este modulo so PARSEIA bytes; nao abre porta nenhuma. O acesso a serial e
responsabilidade de `motion_core/lidar/gerenciador.py` - inclusive o envio
do comando de start. Separar assim permite testar o parser com bytes
sinteticos sem hardware (mesma regra de `tests/unit`).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

#: Prefixo fixo de todo quadro (4 bytes).
HEADER_INICIO = bytes((0x55, 0xAA, 0x83, 0x08))
#: Comprimento maximo de um quadro: 4 + 2 + 2 + (8 * 3) + 2 + 2.
TAMANHO_QUADRO = 36
#: Pontos por quadro - fixado pelo byte 3 (sampleinfo = 0x08).
PONTOS_POR_QUADRO = 8

#: Comandos seriais do COIN-D6 (ver manual oficial).
COMANDO_INICIAR = bytes((0xAA, 0x55, 0xF0, 0x0F))
COMANDO_PARAR = bytes((0xAA, 0x55, 0xF5, 0x0A))
COMANDO_VERSAO = bytes((0xAA, 0x55, 0xFC, 0x03))

#: Base e faixa do codigo de angulo (defines do SDK oficial).
FRAME_ANGLE_BASE = 0xA000
FRAME_ANGLE_RANGE = 90 * 256
#: Unidades de angulo por grau: FRAME_ANGLE_RANGE / 360 = 64.
_ANGULO_POR_UNIDADE = 64.0

#: Formato do struct: 4B header, H speed, H start_angle, 8 x 3B de ponto
#: (d0=dist_low, d1=dist_high, d2=qualidade), H stop_angle, H checksum.
_FORMATO = "<BBBBHH" + "BBB" * PONTOS_POR_QUADRO + "HH"


@dataclass
class PontoLidar:
    """Um ponto da varredura, em coordenadas polares no referencial do LiDAR.

    Angulo em graus [0, 360), distancia em milimetros, confianca 0-63.
    """

    angulo_graus: float
    distancia_mm: int
    confianca: int


@dataclass
class QuadroLidar:
    """Um quadro decodificado, ainda sem os 360 graus montados."""

    velocidade_graus_s: int
    pontos: list[PontoLidar] = field(default_factory=list)
    #: Somas brutas dos codigos de angulo para quem quiser conferir.
    start_angle_raw: int = 0
    end_angle_raw: int = 0


def _codigo_para_graus(codigo: int) -> float:
    """Converte o codigo de angulo do COIN-D6 (base 0xA000, 1/64 grau)."""
    return (codigo - FRAME_ANGLE_BASE) / _ANGULO_POR_UNIDADE


def calcular_checksum(quadro: bytes) -> int:
    """Checksum do COIN-D6 sobre os primeiros 34 bytes do quadro.

    Reproduz o `waitPackage_coin` do SDK oficial: 17 words little-endian
    acumuladas com rotacao a esquerda de 1 bit (int 32 bits com estouro) e
    dobra final dos 15 bits altos nos baixos.
    """
    chk32 = 0
    for i in range(17):
        palavra = quadro[2 * i] | (quadro[2 * i + 1] << 8)
        chk32 = ((chk32 << 1) + palavra) & 0xFFFFFFFF
    chk = (chk32 & 0x7FFF) + (chk32 >> 15)
    return chk & 0x7FFF


def decodificar_quadro(quadro: bytes) -> QuadroLidar | None:
    """Decodifica UM quadro de 36 bytes. None se o quadro for invalido.

    Validacoes: tamanho, header de 4 bytes, contagem de pontos e checksum.
    Nao valida faixa de angulo/distancia - quem consome decide.
    """
    if quadro is None or len(quadro) != TAMANHO_QUADRO:
        return None
    if quadro[:4] != HEADER_INICIO:
        return None
    if calcular_checksum(quadro) != (quadro[34] | (quadro[35] << 8)):
        return None

    desempacotado = struct.unpack(_FORMATO, quadro)
    # Layout: [0..3] header, [4] speed, [5] start_angle, [6..29] 8 x 3B de
    # ponto (d0, d1, d2), [30] stop_angle, [31] checksum.
    velocidade_graus_s = desempacotado[4]
    start_angle_raw = desempacotado[5]
    end_angle_raw = desempacotado[30]

    # Interpolacao linear do angulo de cada ponto. Trata o wrap de 360 graus
    # (end menor que start = cruzou o zero). O SDK divide por (N-1): o
    # ultimo ponto do quadro e o angulo final.
    start = _codigo_para_graus(start_angle_raw)
    end = _codigo_para_graus(end_angle_raw)
    if end < start:
        end += 360.0
    passo = (end - start) / (PONTOS_POR_QUADRO - 1)

    pontos: list[PontoLidar] = []
    for i in range(PONTOS_POR_QUADRO):
        base = 6 + i * 3
        d0 = desempacotado[base]
        d1 = desempacotado[base + 1]
        d2 = desempacotado[base + 2]
        # distance_q2 esta em MILIMETROS (o driver ROS publica /1000 para
        # metros); a mascara 0x3FFF limita aos 14 bits validos do campo.
        distancia_mm = (d0 | (d1 << 8)) & 0x3FFF
        angulo = (start + passo * i) % 360.0
        pontos.append(PontoLidar(angulo_graus=angulo, distancia_mm=distancia_mm, confianca=d2 >> 2))

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