"""Protocolo de mensagens do ORION OS (Cap 5 secao 5; Cap 14 secoes 3-4).

Mesmo formato de mensagem nos dois enlaces (TCP Notebook<->Raspberry e
Serial Raspberry<->Arduino) - so o transporte muda. Cada mensagem carrega:
protocolo, origem, destino, tipo, id unico, timestamp, payload e um
checksum (CRC16) sobre os demais campos.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from orion.communication.crc import crc16

VERSAO_PROTOCOLO = "1.0"

_CAMPOS_OBRIGATORIOS = (
    "protocolo",
    "origem",
    "destino",
    "tipo",
    "id",
    "timestamp",
    "payload",
    "checksum",
)


class TipoMensagem(str, Enum):
    """Cap 14 secao 4."""

    COMMAND = "COMMAND"
    ACK = "ACK"
    NACK = "NACK"
    EVENT = "EVENT"
    TELEMETRY = "TELEMETRY"
    RESPONSE = "RESPONSE"
    HEARTBEAT = "HEARTBEAT"


class ErroProtocoloInvalido(Exception):
    """Mensagem malformada, com tipo/campo invalido ou checksum incorreto."""


def _serializar_para_checksum(campos: dict[str, Any]) -> bytes:
    """Serializacao canonica (chaves ordenadas, sem espacos) para calcular o CRC.

    Usada tanto ao gerar uma mensagem nova quanto ao validar uma recebida -
    o mesmo dict de entrada sempre produz o mesmo checksum.
    """
    return json.dumps(campos, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass
class Mensagem:
    """Uma mensagem do protocolo. Use `Mensagem.nova(...)` para criar uma
    mensagem original (calcula checksum na hora) ou `Mensagem.from_dict(...)`
    para reconstruir uma mensagem recebida pela rede/serial (preserva o
    checksum como veio, para poder validar contra corrupcao)."""

    tipo: TipoMensagem
    origem: str
    destino: str
    payload: dict[str, Any]
    id: str
    timestamp: float
    checksum: str
    protocolo: str = VERSAO_PROTOCOLO
    id_referencia: str | None = None

    @classmethod
    def nova(
        cls,
        tipo: TipoMensagem,
        origem: str,
        destino: str,
        payload: dict[str, Any] | None = None,
        id_referencia: str | None = None,
    ) -> "Mensagem":
        """Cria uma mensagem original: gera id/timestamp e calcula o checksum."""
        campos = {
            "protocolo": VERSAO_PROTOCOLO,
            "origem": origem,
            "destino": destino,
            "tipo": tipo.value,
            "id": uuid.uuid4().hex,
            "timestamp": time.time(),
            "payload": payload or {},
            "id_referencia": id_referencia,
        }
        checksum = f"{crc16(_serializar_para_checksum(campos)):04x}"
        return cls(
            tipo=tipo,
            origem=campos["origem"],
            destino=campos["destino"],
            payload=campos["payload"],
            id=campos["id"],
            timestamp=campos["timestamp"],
            checksum=checksum,
            protocolo=campos["protocolo"],
            id_referencia=id_referencia,
        )

    @classmethod
    def ack(cls, mensagem_original: "Mensagem", origem: str) -> "Mensagem":
        return cls.nova(
            TipoMensagem.ACK,
            origem=origem,
            destino=mensagem_original.origem,
            id_referencia=mensagem_original.id,
        )

    @classmethod
    def nack(cls, mensagem_original: "Mensagem", origem: str, motivo: str) -> "Mensagem":
        return cls.nova(
            TipoMensagem.NACK,
            origem=origem,
            destino=mensagem_original.origem,
            payload={"motivo": motivo},
            id_referencia=mensagem_original.id,
        )

    def _campos_para_checksum(self) -> dict[str, Any]:
        return {
            "protocolo": self.protocolo,
            "origem": self.origem,
            "destino": self.destino,
            "tipo": self.tipo.value,
            "id": self.id,
            "timestamp": self.timestamp,
            "payload": self.payload,
            "id_referencia": self.id_referencia,
        }

    def checksum_esperado(self) -> str:
        return f"{crc16(_serializar_para_checksum(self._campos_para_checksum())):04x}"

    def checksum_valido(self) -> bool:
        return self.checksum == self.checksum_esperado()

    def to_dict(self) -> dict[str, Any]:
        dados = self._campos_para_checksum()
        dados["checksum"] = self.checksum
        return dados

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")

    @classmethod
    def from_dict(cls, dados: dict[str, Any]) -> "Mensagem":
        """Reconstroi uma mensagem recebida. Nao recalcula o checksum - ele
        e preservado como veio, para `checksum_valido()` poder detectar
        corrupcao no transporte."""
        faltando = [campo for campo in _CAMPOS_OBRIGATORIOS if campo not in dados]
        if faltando:
            raise ErroProtocoloInvalido(f"Campos ausentes na mensagem: {faltando}")

        try:
            tipo = TipoMensagem(dados["tipo"])
        except ValueError as erro:
            raise ErroProtocoloInvalido(f"Tipo de mensagem invalido: {dados['tipo']!r}") from erro

        if not isinstance(dados["payload"], dict):
            raise ErroProtocoloInvalido("Campo 'payload' deveria ser um objeto")

        return cls(
            tipo=tipo,
            origem=dados["origem"],
            destino=dados["destino"],
            payload=dados["payload"],
            id=dados["id"],
            timestamp=dados["timestamp"],
            checksum=dados["checksum"],
            protocolo=dados["protocolo"],
            id_referencia=dados.get("id_referencia"),
        )

    @classmethod
    def from_json(cls, texto: str) -> "Mensagem":
        try:
            dados = json.loads(texto)
        except json.JSONDecodeError as erro:
            raise ErroProtocoloInvalido(f"JSON invalido: {erro}") from erro
        if not isinstance(dados, dict):
            raise ErroProtocoloInvalido("Mensagem deveria ser um objeto JSON")
        return cls.from_dict(dados)

    @classmethod
    def from_bytes(cls, dados: bytes) -> "Mensagem":
        return cls.from_json(dados.decode("utf-8"))


#: Cap 14 secao 5: "Todo COMMAND possui id unico; ACK obrigatorio em ate 500 ms."
TIPOS_QUE_EXIGEM_ACK = frozenset({TipoMensagem.COMMAND})
