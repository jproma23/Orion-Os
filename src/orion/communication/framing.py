"""Camada de enquadramento (Cap 14 secao 3).

Serial (Raspberry<->Arduino): delimitadores STX/ETX, escape de bytes
(byte-stuffing) e CRC16 - necessario porque a porta serial e um fluxo de
bytes sem nocao de mensagem. CRC invalido -> descarte silencioso (Cap 14
secao 5); quem chama decide se pede retransmissao (NACK).

TCP (Notebook<->Raspberry): o proprio Cap 14 diz que no TCP "o framing
delimita mensagens no stream" - sem escape/CRC, porque o TCP ja garante
entrega ordenada e integra dos bytes. Usamos prefixo de tamanho (4 bytes),
que delimita sem ambiguidade (evita basear o parsing em varredura de
delimitador, que ja causou um bug de enquadramento no projeto irmao
Sentinela X quando o escape nao existia).
"""
from __future__ import annotations

from orion.communication.crc import crc16

STX = 0x02
ETX = 0x03
ESC = 0x1B
_XOR_ESCAPE = 0x20

_BYTES_ESPECIAIS = (STX, ETX, ESC)

_PREFIXO_TAMANHO_TCP = 4


def codificar_serial(payload: bytes) -> bytes:
    """STX + payload_escapado + crc16_escapado + ETX."""
    crc = crc16(payload).to_bytes(2, "big")
    corpo = bytearray()
    for byte in payload + crc:
        if byte in _BYTES_ESPECIAIS:
            corpo.append(ESC)
            corpo.append(byte ^ _XOR_ESCAPE)
        else:
            corpo.append(byte)
    return bytes([STX]) + bytes(corpo) + bytes([ETX])


class DecodificadorSerial:
    """Decodificador stateful: alimente byte a byte conforme chegam da porta serial."""

    def __init__(self) -> None:
        self._em_quadro = False
        self._escapando = False
        self._buffer = bytearray()
        self.quadros_invalidos = 0

    def alimentar(self, byte: int) -> bytes | None:
        """Processa um byte. Retorna o payload decodificado quando um quadro
        valido se completa, ou None caso contrario (quadro ainda incompleto,
        ou byte de ruido fora de um quadro)."""
        if byte == STX:
            # Reinicia mesmo se ja estava no meio de um quadro - permite
            # ressincronizar apos ruido/quadro incompleto no link serial.
            self._em_quadro = True
            self._escapando = False
            self._buffer = bytearray()
            return None

        if not self._em_quadro:
            return None

        if byte == ETX and not self._escapando:
            self._em_quadro = False
            return self._finalizar_quadro()

        if byte == ESC and not self._escapando:
            self._escapando = True
            return None

        if self._escapando:
            byte ^= _XOR_ESCAPE
            self._escapando = False

        self._buffer.append(byte)
        return None

    def alimentar_bytes(self, dados: bytes) -> list[bytes]:
        """Conveniencia para alimentar varios bytes de uma vez (ex.: em testes)."""
        quadros = []
        for byte in dados:
            quadro = self.alimentar(byte)
            if quadro is not None:
                quadros.append(quadro)
        return quadros

    def _finalizar_quadro(self) -> bytes | None:
        if len(self._buffer) < 2:
            self.quadros_invalidos += 1
            return None
        payload, crc_recebido = bytes(self._buffer[:-2]), bytes(self._buffer[-2:])
        if crc_recebido != crc16(payload).to_bytes(2, "big"):
            self.quadros_invalidos += 1
            return None
        return payload


def codificar_tcp(payload: bytes) -> bytes:
    """Prefixo de tamanho (4 bytes, big-endian) + payload."""
    return len(payload).to_bytes(_PREFIXO_TAMANHO_TCP, "big") + payload


class DecodificadorTcp:
    """Decodificador stateful para o framing por prefixo de tamanho do TCP."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def alimentar(self, dados: bytes) -> list[bytes]:
        """Adiciona bytes recebidos do socket e retorna as mensagens completas."""
        self._buffer.extend(dados)
        mensagens = []
        while True:
            if len(self._buffer) < _PREFIXO_TAMANHO_TCP:
                break
            tamanho = int.from_bytes(self._buffer[:_PREFIXO_TAMANHO_TCP], "big")
            fim = _PREFIXO_TAMANHO_TCP + tamanho
            if len(self._buffer) < fim:
                break
            mensagens.append(bytes(self._buffer[_PREFIXO_TAMANHO_TCP:fim]))
            del self._buffer[:fim]
        return mensagens
