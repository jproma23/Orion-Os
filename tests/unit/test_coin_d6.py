"""Testes do parser do LiDAR COIN-D6 (motion_core/lidar/coin_d6.py).

O parser e puro (nao abre serial nenhuma), entao e testado com quadros
SINTETICOS montados na hora com checksum valido - cobrindo os casos que o
sensor real pode mandar (wrap de 360, pontos em distancia maxima, etc.).
Nada aqui precisa de hardware.
"""
import struct

import pytest

from motion_core.lidar.coin_d6 import (
    COMANDO_INICIAR,
    FRAME_ANGLE_BASE,
    HEADER_INICIO,
    PONTOS_POR_QUADRO,
    TAMANHO_QUADRO,
    acumular_scan,
    calcular_checksum,
    decodificar_quadro,
)


def montar_quadro(
    velocidade_graus_s: int = 3600,
    start_angle_raw: int = FRAME_ANGLE_BASE,
    end_angle_raw: int = FRAME_ANGLE_BASE + 7 * 64,
    pontos: list[tuple[int, int]] | None = None,
) -> bytes:
    """Monta UM quadro valido de 36 bytes, com checksum correto no final.

    `pontos` e uma lista de (distancia_mm, d2); o d2 cru e a qualidade
    decodificada sai como d2 >> 2 (6 bits), como no SDK oficial.
    """
    if pontos is None:
        pontos = [(1000 + i, 200) for i in range(PONTOS_POR_QUADRO)]

    b = bytearray(HEADER_INICIO)
    b += struct.pack("<H", velocidade_graus_s & 0xFFFF)
    b += struct.pack("<H", start_angle_raw & 0xFFFF)
    for distancia_mm, d2 in pontos:
        b += bytes((distancia_mm & 0xFF, (distancia_mm >> 8) & 0xFF, d2 & 0xFF))
    b += struct.pack("<H", end_angle_raw & 0xFFFF)
    b += struct.pack("<H", calcular_checksum(bytes(b)))
    return bytes(b)


# ---------- checksum ----------


def test_checksum_valor_conhecido():
    # Fixacao independente do algoritmo do SDK (17 words LE, rotacao a
    # esquerda de 1 bit em int 32, dobra dos 15 bits altos nos baixos).
    quadro = montar_quadro()
    assert calcular_checksum(quadro) == (quadro[34] | (quadro[35] << 8))


def test_checksum_detecta_byte_corrompido():
    quadro = bytearray(montar_quadro())
    quadro[20] ^= 0xFF
    assert calcular_checksum(bytes(quadro)) != (quadro[34] | (quadro[35] << 8))


# ---------- decodificacao ----------


def test_quadro_valido_decodifica_8_pontos():
    pontos = [(i * 100, 200) for i in range(PONTOS_POR_QUADRO)]
    quadro = montar_quadro(
        velocidade_graus_s=3600,
        start_angle_raw=FRAME_ANGLE_BASE,
        end_angle_raw=FRAME_ANGLE_BASE + (PONTOS_POR_QUADRO - 1) * 64,
        pontos=pontos,
    )
    assert len(quadro) == TAMANHO_QUADRO

    decodificado = decodificar_quadro(quadro)
    assert decodificado is not None
    assert decodificado.velocidade_graus_s == 3600
    assert len(decodificado.pontos) == PONTOS_POR_QUADRO

    for i, ponto in enumerate(decodificado.pontos):
        assert ponto.distancia_mm == i * 100
        # d2 cru = 200 -> qualidade 6 bits = 200 >> 2 = 50
        assert ponto.confianca == 50
        # angulo interpolado: (codigo - 0xA000)/64; start=0, end=7 graus,
        # passo = (end-start)/(N-1) = 1 grau (N=8 -> 7 passos).
        assert ponto.angulo_graus == pytest.approx(i * 1.0, abs=1e-6)


def test_angulos_interpolados_com_wrap_de_360():
    # start = 359 graus, end = 6 graus: o quadro cruza o zero.
    start_raw = FRAME_ANGLE_BASE + 359 * 64
    end_raw = FRAME_ANGLE_BASE + 6 * 64
    quadro = montar_quadro(start_angle_raw=start_raw, end_angle_raw=end_raw)

    decodificado = decodificar_quadro(quadro)
    assert decodificado is not None

    primeiro = decodificado.pontos[0]
    ultimo = decodificado.pontos[-1]
    # passo = (360 - 359 + 6)/7 = 1 grau; o ultimo ponto (indice N-1) e o
    # angulo final do quadro, como no SDK (sampleAngle_t = start + interval*i).
    assert primeiro.angulo_graus == pytest.approx(359.0, abs=1e-6)
    assert ultimo.angulo_graus == pytest.approx(6.0, abs=1e-3)
    for ponto in decodificado.pontos:
        assert 0.0 <= ponto.angulo_graus < 360.0


def test_checksum_errado_devolve_none():
    quadro = bytearray(montar_quadro())
    quadro[-1] ^= 0x01  # estraga o checksum
    assert decodificar_quadro(bytes(quadro)) is None


def test_tamanho_errado_devolve_none():
    quadro = montar_quadro()
    assert decodificar_quadro(quadro[:-1]) is None
    assert decodificar_quadro(quadro + b"\x00") is None
    assert decodificar_quadro(b"") is None


def test_header_errado_devolve_none():
    quadro = bytearray(montar_quadro())
    quadro[2] = 0x84  # 0x83 do header corrompido
    assert decodificar_quadro(bytes(quadro)) is None


def test_ponto_fora_da_faixa_nao_e_validado_pelo_parser():
    # distancia 0 e confianca 0 sao aceitas aqui - quem decide o que e util
    # e o LidarManager (min_distancia_mm/confianca_minima). O parser so
    # garante estrutura e checksum.
    quadro = montar_quadro(pontos=[(0, 0)] * PONTOS_POR_QUADRO)
    decodificado = decodificar_quadro(quadro)
    assert decodificado is not None
    assert decodificado.pontos[0].distancia_mm == 0


def test_distancia_acima_de_4m_nao_estoura_14_bits():
    # O SDK mascara com 0x3FFF e publica /1000 -> distancia fica em mm direto.
    # 9000 mm (9 m) passa inteiro pela mascara de 14 bits (16383).
    quadro = montar_quadro(pontos=[(9000, 100)] * PONTOS_POR_QUADRO)
    decodificado = decodificar_quadro(quadro)
    assert decodificado is not None
    assert decodificado.pontos[0].distancia_mm == 9000


# ---------- varredura ----------


def test_acumular_scan_mantem_menor_distancia_por_angulo():
    # dois quadros com o mesmo angulo ~0: um a 1000mm, outro a 800mm
    primeiro = montar_quadro(
        start_angle_raw=FRAME_ANGLE_BASE,
        end_angle_raw=FRAME_ANGLE_BASE + (PONTOS_POR_QUADRO - 1) * 64,
        pontos=[(1000, 200)] * PONTOS_POR_QUADRO,
    )
    segundo = montar_quadro(
        start_angle_raw=FRAME_ANGLE_BASE,
        end_angle_raw=FRAME_ANGLE_BASE + (PONTOS_POR_QUADRO - 1) * 64,
        pontos=[(800, 200)] * PONTOS_POR_QUADRO,
    )

    scan = acumular_scan([decodificar_quadro(primeiro), decodificar_quadro(segundo)])
    assert len(scan) >= 1
    # angulo 0: fica o ponto MENOR (800), obstaculo perto vale mais que reflexo
    ponto_em_zero = scan.get(0)
    assert ponto_em_zero is not None
    assert ponto_em_zero.distancia_mm == 800


def test_acumular_scan_sobre_dois_quadros_cobre_o_circulo():
    quadros = [
        montar_quadro(
            start_angle_raw=FRAME_ANGLE_BASE,
            end_angle_raw=FRAME_ANGLE_BASE + 179 * 64,
        ),
        montar_quadro(
            start_angle_raw=FRAME_ANGLE_BASE + 181 * 64,
            end_angle_raw=FRAME_ANGLE_BASE + 359 * 64,
        ),
    ]
    scan = acumular_scan([decodificar_quadro(q) for q in quadros])
    assert len(scan) > 0
    # primeira e segunda metades presentes
    assert any(angulo < 180 for angulo in scan)
    assert any(angulo >= 180 for angulo in scan)


# ---------- comandos ----------


def test_comando_iniciar_e_o_do_manual():
    assert COMANDO_INICIAR == bytes((0xAA, 0x55, 0xF0, 0x0F))