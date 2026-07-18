"""Testes do protocolo de mensagens (Cap 5 s.5; Cap 14 s.3-4)."""
import pytest

from orion.communication.crc import crc16
from orion.communication.protocol import (
    ErroProtocoloInvalido,
    Mensagem,
    TipoMensagem,
)


def test_crc16_vetor_de_teste_conhecido():
    assert crc16(b"123456789") == 0x29B1


def test_mensagem_nova_tem_checksum_valido():
    msg = Mensagem.nova(TipoMensagem.COMMAND, "mission_core", "motion_core", {"acao": "MOVE_TO"})
    assert msg.checksum_valido()


def test_roundtrip_json():
    original = Mensagem.nova(TipoMensagem.EVENT, "motion_core", "mission_core", {"x": 1, "y": 2})
    reconstruida = Mensagem.from_json(original.to_json())

    assert reconstruida.checksum_valido()
    assert reconstruida.to_dict() == original.to_dict()


def test_checksum_invalido_apos_corrupcao():
    original = Mensagem.nova(TipoMensagem.EVENT, "motion_core", "mission_core", {"x": 1})
    dados = original.to_dict()
    dados["payload"] = {"x": 999}  # simula corrupcao no transporte
    corrompida = Mensagem.from_dict(dados)

    assert corrompida.checksum_valido() is False


def test_ack_referencia_id_da_mensagem_original():
    comando = Mensagem.nova(TipoMensagem.COMMAND, "mission_core", "motion_core", {"acao": "STOP"})
    ack = Mensagem.ack(comando, origem="motion_core")

    assert ack.tipo is TipoMensagem.ACK
    assert ack.id_referencia == comando.id
    assert ack.destino == comando.origem
    assert ack.checksum_valido()


def test_nack_carrega_motivo():
    comando = Mensagem.nova(TipoMensagem.COMMAND, "mission_core", "motion_core", {"acao": "STOP"})
    nack = Mensagem.nack(comando, origem="motion_core", motivo="crc_invalido")

    assert nack.tipo is TipoMensagem.NACK
    assert nack.payload == {"motivo": "crc_invalido"}


def test_tipo_invalido_falha():
    dados = Mensagem.nova(TipoMensagem.EVENT, "a", "b", {}).to_dict()
    dados["tipo"] = "TIPO_INEXISTENTE"
    with pytest.raises(ErroProtocoloInvalido, match="Tipo de mensagem invalido"):
        Mensagem.from_dict(dados)


def test_campo_ausente_falha():
    dados = Mensagem.nova(TipoMensagem.EVENT, "a", "b", {}).to_dict()
    del dados["timestamp"]
    with pytest.raises(ErroProtocoloInvalido, match="timestamp"):
        Mensagem.from_dict(dados)


def test_json_malformado_falha():
    with pytest.raises(ErroProtocoloInvalido):
        Mensagem.from_json("{nao e json valido")


def test_payload_deve_ser_objeto():
    dados = Mensagem.nova(TipoMensagem.EVENT, "a", "b", {}).to_dict()
    dados["payload"] = "nao e um objeto"
    with pytest.raises(ErroProtocoloInvalido, match="payload"):
        Mensagem.from_dict(dados)
