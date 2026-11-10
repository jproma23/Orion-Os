// Protocolo de mensagens (Cap 5 secao 5) do lado do firmware.
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

namespace orion {

// QUAL COMMIT ESTA NA PLACA (2026-11-10, auditoria C4-07). Ate aqui nenhum
// .bin dizia de onde veio: VERSAO_FIRMWARE ficou "0.1.0-fase2" por meses de
// mudanca, e "o que esta gravado no corpo?" so se respondia regravando. O
// extra_script versao_git.py (platformio.ini) injeta o commit curto do git
// na compilacao, com "-dirty" se firmware/hardware_core tinha mudanca nao
// commitada. Sem git (copia solta, maquina sem git) o build NAO quebra: cai
// aqui e a placa diz "desconhecido" - que tambem e informacao.
#ifndef ORION_GIT_SHA
#define ORION_GIT_SHA "desconhecido"
#endif

constexpr const char* VERSAO_PROTOCOLO = "1.0";
constexpr const char* NOME_MODULO = "hardware_core";
//: Vai em WHO_ARE_YOU como `versao_modulo` - e o Pi ja loga esse campo na
//: descoberta ("Descoberta OK ... versao_modulo=0.1.0-fase2+<sha>").
constexpr const char* VERSAO_FIRMWARE = "0.1.0-fase2+" ORION_GIT_SHA;
//: So o commit, para quem quiser comparar com `git log` sem partir a string.
//: Vai em WHO_ARE_YOU e na telemetria completa como `firmware_git`.
constexpr const char* COMMIT_FIRMWARE = ORION_GIT_SHA;

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
