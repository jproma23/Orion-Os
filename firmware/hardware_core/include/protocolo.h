// Protocolo de mensagens (Cap 5 secao 5) do lado do firmware.
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

namespace orion {

constexpr const char* VERSAO_PROTOCOLO = "1.0";
constexpr const char* NOME_MODULO = "hardware_core";
constexpr const char* VERSAO_FIRMWARE = "0.1.0-fase2";

// Gera um id de mensagem (millis + contador, em hex) - unico o bastante
// para correlacionar ACK/RESPONSE com o pedido original.
String gerarIdMensagem();

// Monta e envia uma mensagem completa (Cap 5 s.5) na porta indicada.
//
// O campo "checksum" e um CRC16 sobre o proprio id da mensagem - nao
// precisa reproduzir o algoritmo de checksum do lado Python: o Raspberry
// usa exigir_checksum_mensagem=False para o link serial e confia no CRC16
// da camada de enquadramento (ja verificado antes do quadro chegar aqui)
// para garantir a integridade, evitando depender de uma serializacao JSON
// canonica identica entre C++ e Python.
void enviarMensagem(Stream& porta, const char* tipo, const char* destino,
                     JsonObjectConst payload, const char* idReferencia = nullptr);

}  // namespace orion
