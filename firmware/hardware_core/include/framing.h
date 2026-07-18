// Camada de enquadramento (Cap 14 secao 3): STX/ETX + byte-stuffing + CRC16.
// Mesmo algoritmo do lado Python (orion.communication.framing) - CRC16
// simples e byte a byte, sem ambiguidade entre linguagens.
#pragma once

#include <Arduino.h>

namespace orion {

constexpr uint8_t FRAME_STX = 0x02;
constexpr uint8_t FRAME_ETX = 0x03;
constexpr uint8_t FRAME_ESC = 0x1B;
constexpr uint8_t FRAME_XOR_ESCAPE = 0x20;

uint16_t crc16(const uint8_t* dados, size_t tamanho);

// Escreve STX + payload/CRC escapados + ETX diretamente na porta.
void enviarQuadro(Stream& porta, const uint8_t* payload, size_t tamanho);
void enviarQuadro(Stream& porta, const String& payload);

// Decodificador stateful: alimente byte a byte conforme chegam da porta.
class DecodificadorQuadro {
 public:
  static constexpr size_t CAPACIDADE = 320;

  // Retorna true quando um quadro valido termina de chegar (payload em
  // buffer()/tamanho()). Quadros com CRC invalido sao descartados
  // silenciosamente (contam em quadrosInvalidos()) - Cap 14 secao 5.
  bool alimentar(uint8_t byte);

  const uint8_t* buffer() const { return _buffer; }
  size_t tamanho() const { return _tamanho; }
  uint32_t quadrosInvalidos() const { return _quadrosInvalidos; }

 private:
  uint8_t _buffer[CAPACIDADE];
  size_t _tamanho = 0;
  bool _emQuadro = false;
  bool _escapando = false;
  uint32_t _quadrosInvalidos = 0;

  bool finalizarQuadro();
};

}  // namespace orion
