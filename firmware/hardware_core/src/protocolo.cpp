#include "protocolo.h"

#include "framing.h"

namespace orion {

namespace {
uint32_t contadorId = 0;
}

String gerarIdMensagem() {
  contadorId++;
  char buf[20];
  snprintf(buf, sizeof(buf), "%08lx%04x", millis(), static_cast<unsigned>(contadorId & 0xFFFF));
  return String(buf);
}

void enviarMensagem(Stream& porta, const char* tipo, const char* destino,
                     JsonObjectConst payload, const char* idReferencia) {
  JsonDocument doc;
  doc["protocolo"] = VERSAO_PROTOCOLO;
  doc["origem"] = NOME_MODULO;
  doc["destino"] = destino;
  doc["tipo"] = tipo;

  String id = gerarIdMensagem();
  doc["id"] = id;
  doc["timestamp"] = millis() / 1000.0;
  doc["payload"] = payload;
  doc["id_referencia"] = idReferencia;  // ArduinoJson serializa ponteiro nulo como JSON null

  uint16_t crc = crc16(reinterpret_cast<const uint8_t*>(id.c_str()), id.length());
  char crcHex[5];
  snprintf(crcHex, sizeof(crcHex), "%04x", crc);
  doc["checksum"] = crcHex;

  String corpo;
  serializeJson(doc, corpo);
  enviarQuadro(porta, corpo);
}

}  // namespace orion
