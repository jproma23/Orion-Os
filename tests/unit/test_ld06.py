"""Testes do parser do LiDAR LD06 (motion_core/lidar/ld06.py).

O parser e puro (nao abre serial nenhuma), entao e testado com quadros
SINTETICOS montados na hora com CRC valido - cobrindo os casos que o sensor
real pode mandar (wrap de 360, pontos em distancia maxima, etc.). Nada aqui
precisa de hardware.
"""
import struct

import pytest

from motion_core.lidar.ld06 import (
    HEADER,
    PONTOS_POR_QUADRO,
    TAMANHO_QUADRO,
    acumular_scan,
    crc8_ld06,
    decodificar_quadro,
)

VER_LEN_12_PONTOS = 0x2C  # 0x2C: versao 1 (5 bits altos) + 12 pontos (5 baixos)


def montar_quadro(
    velocidade_graus_s: int = 1200,
    start_angle_raw: int = 0,
    end_angle_raw: int = 3600,
    pontos: list[tuple[int, int]] | None = None,
    timestamp_ms: int = 1234,
    ver_len: int = VER_LEN_12_PONTOS,
) -> bytes:
    """Monta UM quadro valido de 47 bytes, com CRC8 correto no final."""
    if pontos is None:
        pontos = [(1000 + i, 200) for i in range(PONTOS_POR_QUADRO)]

    b = bytearray()
    b.append(HEADER)
    b.append(ver_len)
    b += struct.pack("<H", velocidade_graus_s & 0xFFFF)
    b += struct.pack("<H", start_angle_raw & 0xFFFF)
    for distancia, confianca in pontos:
        b += struct.pack("<H", distancia & 0xFFFF)
        b.append(confianca & 0xFF)
    b += struct.pack("<H", end_angle_raw & 0xFFFF)
    b += struct.pack("<H", timestamp_ms & 0xFFFF)
    b.append(crc8_ld06(bytes(b)))
    return bytes(b)


# ---------- CRC8 ----------


def test_crc8_valores_conhecidos():
    # Valor de fixacao da tabela 0x4D/init 0x00, conferido por implementacao
    # independente bit-a-bit: crc(b"\x54") = 0xEE e o quadro abre com 0x54
    # 0x2C, cujo CRC e 0xD8. Mudou a tabela? Este teste quebra.
    assert crc8_ld06(bytes([HEADER])) == 0xEE
    assert crc8_ld06(bytes([HEADER, 0x2C])) == 0xD8


def test_crc_detecta_byte_corrompido():
    quadro = bytearray(montar_quadro())
    quadro[20] ^= 0xFF
    assert crc8_ld06(bytes(quadro[:46])) != bytes(quadro)[46]


# ---------- decodificacao ----------


def test_quadro_valido_decodifica_12_pontos():
    pontos = [(i * 100, 200) for i in range(PONTOS_POR_QUADRO)]
    quadro = montar_quadro(
        velocidade_graus_s=1200,
        start_angle_raw=0,
        end_angle_raw=3600,
        pontos=pontos,
    )
    assert len(quadro) == TAMANHO_QUADRO

    decodificado = decodificar_quadro(quadro)
    assert decodificado is not None
    assert decodificado.velocidade_graus_s == 1200
    assert len(decodificado.pontos) == PONTOS_POR_QUADRO

    for i, ponto in enumerate(decodificado.pontos):
        assert ponto.distancia_mm == i * 100
        assert ponto.confianca == 200
        # angulo interpolado: 0 ate 36 graus, passo de 3 graus
        assert ponto.angulo_graus == pytest.approx(i * 3.0, abs=1e-6)


def test_angulos_interpolados_com_wrap_de_360():
    # start = 35000 (350 graus), end = 1000 (10 graus): o quadro cruza o zero.
    quadro = montar_quadro(start_angle_raw=35000, end_angle_raw=1000)

    decodificado = decodificar_quadro(quadro)
    assert decodificado is not None

    primeiro = decodificado.pontos[0]
    ultimo = decodificado.pontos[-1]
    # passo = (37000 - 35000)/12 = 166,67 centesimos = 1,6667 graus
    assert primeiro.angulo_graus == pytest.approx(350.0, abs=1e-6)
    assert ultimo.angulo_graus == pytest.approx(8.3333, abs=1e-3)
    for ponto in decodificado.pontos:
        assert 0.0 <= ponto.angulo_graus < 360.0


def test_crc_errado_devolve_none():
    quadro = bytearray(montar_quadro())
    quadro[-1] ^= 0x01  # estraga o CRC
    assert decodificar_quadro(bytes(quadro)) is None


def test_tamanho_errado_devolve_none():
    quadro = montar_quadro()
    assert decodificar_quadro(quadro[:-1]) is None
    assert decodificar_quadro(quadro + b"\x00") is None
    assert decodificar_quadro(b"") is None


def test_header_errado_devolve_none():
    quadro = bytearray(montar_quadro())
    quadro[0] = 0x53
    assert decodificar_quadro(bytes(quadro)) is None


def test_contagem_de_pontos_diferente_devolve_none():
    # ver_len com 10 pontos nos 5 bits baixos: 0x2A (10) mesmo com o corpo
    # carregando 12 - o parser tem que recusar, nao ler o corpo torto.
    quadro = montar_quadro(ver_len=0x2A)
    assert decodificar_quadro(quadro) is None


def test_ponto_fora_da_faixa_nao_e_validado_pelo_parser():
    # distancia 0 e confianca 0 sao aceitas aqui - quem decide o que e util
    # e o LidarManager (min_distancia_mm/confianca_minima). O parser so
    # garante estrutura e CRC.
    quadro = montar_quadro(pontos=[(0, 0)] * PONTOS_POR_QUADRO)
    decodificado = decodificar_quadro(quadro)
    assert decodificado is not None
    assert decodificado.pontos[0].distancia_mm == 0


# ---------- varredura ----------


def test_acumular_scan_mantem_menor_distancia_por_angulo():
    # dois quadros com o mesmo angulo ~0: um a 1000mm, outro a 800mm
    primeiro = montar_quadro(start_angle_raw=0, end_angle_raw=3600, pontos=[(1000, 200)] * 12)
    segundo = montar_quadro(start_angle_raw=0, end_angle_raw=3600, pontos=[(800, 200)] * 12)

    scan = acumular_scan([decodificar_quadro(primeiro), decodificar_quadro(segundo)])
    assert len(scan) >= 1
    # angulo 0: fica o ponto MENOR (800), obstaculo perto vale mais que reflexo
    ponto_em_zero = scan.get(0)
    assert ponto_em_zero is not None
    assert ponto_em_zero.distancia_mm == 800


def test_acumular_scan_sobre_dois_quadros_cobre_o_circulo():
    quadros = [
        montar_quadro(start_angle_raw=0, end_angle_raw=18000),
        montar_quadro(start_angle_raw=18000, end_angle_raw=36000),
    ]
    scan = acumular_scan([decodificar_quadro(q) for q in quadros])
    assert len(scan) > 0
    # primeira e segunda metades presentes
    assert any(angulo < 180 for angulo in scan)
    assert any(angulo >= 180 for angulo in scan)
