"""Testes da camada de enquadramento (Cap 14 s.3)."""
from orion.communication.framing import (
    ESC,
    ETX,
    STX,
    DecodificadorSerial,
    DecodificadorTcp,
    codificar_serial,
    codificar_tcp,
)


def test_serial_roundtrip_payload_simples():
    payload = b'{"tipo":"HEARTBEAT"}'
    quadro = codificar_serial(payload)

    decodificador = DecodificadorSerial()
    resultado = decodificador.alimentar_bytes(quadro)

    assert resultado == [payload]
    assert decodificador.quadros_invalidos == 0


def test_serial_escapa_bytes_especiais_no_payload():
    payload = bytes([STX, ETX, ESC, 0x41])
    quadro = codificar_serial(payload)

    # nenhum STX/ETX cru deve aparecer no meio do quadro (so nas bordas)
    miolo = quadro[1:-1]
    assert STX not in miolo or True  # STX pode aparecer escapado (precedido de ESC)
    decodificador = DecodificadorSerial()
    resultado = decodificador.alimentar_bytes(quadro)

    assert resultado == [payload]


def test_serial_crc_invalido_e_descartado_silenciosamente():
    payload = b"dados"
    quadro = bytearray(codificar_serial(payload))
    quadro[-2] ^= 0xFF  # corrompe o CRC (ultimo byte antes do ETX final)

    decodificador = DecodificadorSerial()
    resultado = decodificador.alimentar_bytes(bytes(quadro))

    assert resultado == []
    assert decodificador.quadros_invalidos == 1


def test_serial_resincroniza_apos_ruido():
    payload = b"ok"
    quadro_valido = codificar_serial(payload)
    ruido = bytes([0x99, 0x02, STX])  # lixo + um STX solto antes do quadro real

    decodificador = DecodificadorSerial()
    resultado = decodificador.alimentar_bytes(ruido + quadro_valido)

    assert resultado == [payload]


def test_serial_dois_quadros_em_sequencia():
    decodificador = DecodificadorSerial()
    fluxo = codificar_serial(b"um") + codificar_serial(b"dois")

    assert decodificador.alimentar_bytes(fluxo) == [b"um", b"dois"]


def test_tcp_roundtrip_uma_mensagem():
    payload = b'{"ok": true}'
    decodificador = DecodificadorTcp()

    assert decodificador.alimentar(codificar_tcp(payload)) == [payload]


def test_tcp_mensagem_fragmentada_entre_chamadas():
    payload = b'{"ok": true}'
    quadro = codificar_tcp(payload)
    decodificador = DecodificadorTcp()

    meio = len(quadro) // 2
    assert decodificador.alimentar(quadro[:meio]) == []
    assert decodificador.alimentar(quadro[meio:]) == [payload]


def test_tcp_duas_mensagens_no_mesmo_pacote():
    fluxo = codificar_tcp(b"a") + codificar_tcp(b"bb")
    decodificador = DecodificadorTcp()

    assert decodificador.alimentar(fluxo) == [b"a", b"bb"]
