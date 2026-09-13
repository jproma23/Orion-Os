#include "framing.h"

namespace orion {

namespace {
constexpr uint16_t POLINOMIO = 0x1021;
constexpr uint16_t VALOR_INICIAL = 0xFFFF;
}  // namespace

uint16_t crc16(const uint8_t* dados, size_t tamanho) {
  uint16_t crc = VALOR_INICIAL;
  for (size_t i = 0; i < tamanho; i++) {
    crc ^= static_cast<uint16_t>(dados[i]) << 8;
    for (uint8_t bit = 0; bit < 8; bit++) {
      if (crc & 0x8000) {
        crc = (crc << 1) ^ POLINOMIO;
      } else {
        crc = crc << 1;
      }
    }
  }
  return crc;
}

void enviarQuadro(Stream& porta, const uint8_t* payload, size_t tamanho) {
  uint16_t crc = crc16(payload, tamanho);
  uint8_t crcBytes[2] = {static_cast<uint8_t>(crc >> 8), static_cast<uint8_t>(crc & 0xFF)};

  porta.write(FRAME_STX);
  for (size_t i = 0; i < tamanho; i++) {
    uint8_t b = payload[i];
    if (b == FRAME_STX || b == FRAME_ETX || b == FRAME_ESC) {
      porta.write(FRAME_ESC);
      porta.write(static_cast<uint8_t>(b ^ FRAME_XOR_ESCAPE));
    } else {
      porta.write(b);
    }
  }
  for (uint8_t b : crcBytes) {
    if (b == FRAME_STX || b == FRAME_ETX || b == FRAME_ESC) {
      porta.write(FRAME_ESC);
      porta.write(static_cast<uint8_t>(b ^ FRAME_XOR_ESCAPE));
    } else {
      porta.write(b);
    }
  }
  porta.write(FRAME_ETX);
}

void enviarQuadro(Stream& porta, const String& payload) {
  enviarQuadro(porta, reinterpret_cast<const uint8_t*>(payload.c_str()), payload.length());
}

bool DecodificadorQuadro::alimentar(uint8_t byte) {
  if (byte == FRAME_STX) {
    // Reinicia mesmo no meio de um quadro - permite ressincronizar apos
    // ruido/quadro incompleto no link serial.
    _emQuadro = true;
    _escapando = false;
    _tamanho = 0;
    return false;
  }

  if (!_emQuadro) {
    return false;
  }

  if (byte == FRAME_ETX && !_escapando) {
    _emQuadro = false;
    return finalizarQuadro();
  }

  if (byte == FRAME_ESC && !_escapando) {
    _escapando = true;
    return false;
  }

  if (_escapando) {
    byte ^= FRAME_XOR_ESCAPE;
    _escapando = false;
  }

  if (_tamanho < CAPACIDADE) {
    _buffer[_tamanho++] = byte;
  } else {
    // buffer estourado - descarta o quadro (Cap 14 s.5: erro isolado, nao crash)
    _emQuadro = false;
    _quadrosInvalidos++;
  }
  return false;
}

bool DecodificadorQuadro::finalizarQuadro() {
  if (_tamanho < 2) {
    _quadrosInvalidos++;
    return false;
  }
  size_t tamanhoPayload = _tamanho - 2;
  uint16_t crcRecebido = (static_cast<uint16_t>(_buffer[tamanhoPayload]) << 8) |
                         static_cast<uint16_t>(_buffer[tamanhoPayload + 1]);
  uint16_t crcEsperado = crc16(_buffer, tamanhoPayload);
  if (crcRecebido != crcEsperado) {
    _quadrosInvalidos++;
    return false;
  }
  _tamanho = tamanhoPayload;  // deixa so o payload visivel em buffer()/tamanho()
  return true;
}

}  // namespace orion
