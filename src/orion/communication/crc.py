"""CRC16 usado no protocolo (Cap 5 s.5) e no enquadramento serial (Cap 14 s.3).

Mesmo algoritmo (CRC-16/CCITT-FALSE) nos dois lugares: checksum da mensagem
e CRC do quadro serial - evita manter duas implementacoes de CRC no projeto.
"""
from __future__ import annotations

_POLINOMIO = 0x1021
_VALOR_INICIAL = 0xFFFF


def crc16(dados: bytes) -> int:
    """CRC-16/CCITT-FALSE. Vetor de teste conhecido: crc16(b"123456789") == 0x29B1."""
    crc = _VALOR_INICIAL
    for byte in dados:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ _POLINOMIO) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc
