// Hardware Core - firmware completo (Cap 10).
//
// Serial (pinos 0/1, USB) e EXCLUSIVA do protocolo com o Raspberry - nunca
// usar Serial.print() aqui para depuracao, corromperia o enquadramento
// binario.
//
// Fiacao confirmada nesta montagem (guia de ligacao eletrica, atualizado
// 2026-07-18): motores (pinos 2-6), ultrassom frontal fixo (22/23),
// ultrassom traseiro (26/27), IMU I2C (20/21), DHT (24), servo do radar
// (9) e servos pan/tilt (10/11). Encoders e LED continuam RESERVADOS -
// ver pins.h - ainda nao ligados fisicamente.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <string.h>

#include "bateria_manager.h"
#include "command_executor.h"
#include "dht_manager.h"
#include "encoder_manager.h"
#include "estado.h"
#include "framing.h"
#include "imu_manager.h"
#include "motor_manager.h"
#include "pins.h"
#include "protocolo.h"
#include "radar_manager.h"
#include "safety_manager.h"
#include "sensor_ultrassonico.h"
#include "telemetry_manager.h"

// Simbolos do heap do avr-libc (malloc.c) - precisam ficar no escopo global,
// fora do namespace anonimo abaixo, senao o linker procura por
// "(anonymous namespace)::__heap_start" e falha.
extern int __heap_start, *__brkval;

namespace {

orion::DecodificadorQuadro decodificador;
orion::MaquinaDeEstados estados;
orion::MotorManager motores;
orion::EncoderManager encoders;
orion::RadarManager radar;
orion::ImuManager imu;
orion::DhtManager dht;
orion::BateriaManager bateria;
orion::SafetyManager safety;
orion::SensorUltrassonico ultrassomTraseiro;
orion::CommandExecutor comandos(motores, radar, estados);
orion::TelemetryManager telemetria(motores, radar, imu, dht, estados, ultrassomTraseiro,
                                  bateria);

constexpr unsigned long INTERVALO_HEARTBEAT_MS = 1000;
constexpr unsigned long INTERVALO_TELEMETRIA_MS = 500;
constexpr unsigned long INTERVALO_IMU_MS = 50;

unsigned long ultimoHeartbeat = 0;
unsigned long ultimaTelemetria = 0;
unsigned long ultimoImu = 0;
unsigned long ultimoComandoRecebido = 0;
orion::Estado estadoAnterior = orion::Estado::BOOT;
bool varrendoAnterior = false;

JsonDocument g_payloadVazio;  // reutilizado para ACK/HEARTBEAT (sempre {})

void enviarPayloadVazio(const char* tipo, const char* destino, const char* idReferencia = nullptr) {
  orion::enviarMensagem(Serial, tipo, destino, g_payloadVazio.as<JsonObjectConst>(), idReferencia);
}

void notificarTransicaoDeEstado() {
  if (estados.atual() == estadoAnterior) return;
  JsonDocument payload;
  payload["topico"] = "motion.status";
  payload["estado"] = orion::nomeEstado(estados.atual());
  orion::enviarMensagem(Serial, "EVENT", "motion_core", payload.as<JsonObjectConst>());
  estadoAnterior = estados.atual();
}

void notificarFimDeVarredura() {
  bool varrendoAgora = radar.varrendo();
  if (varrendoAnterior && !varrendoAgora) {
    JsonDocument payload;
    payload["topico"] = "motion.scan_complete";
    JsonArray leituras = payload["leituras"].to<JsonArray>();
    for (uint8_t i = 0; i < radar.quantidadeLeituras(); i++) {
      JsonObject item = leituras.add<JsonObject>();
      item["angulo"] = radar.leituras()[i].angulo;
      item["distancia_cm"] = radar.leituras()[i].distanciaCm;
      item["valida"] = radar.leituras()[i].valida;
    }
    orion::enviarMensagem(Serial, "EVENT", "motion_core", payload.as<JsonObjectConst>());
  }
  varrendoAnterior = varrendoAgora;
}

void responderWhoAreYou(const char* origem, const char* idMsg) {
  JsonDocument payload;
  payload["nome"] = orion::NOME_MODULO;
  payload["versao_modulo"] = orion::VERSAO_FIRMWARE;
  payload["versao_protocolo"] = orion::VERSAO_PROTOCOLO;
  orion::enviarMensagem(Serial, "RESPONSE", origem, payload.as<JsonObjectConst>(), idMsg);
}

void responderReturnStatus(const char* origem, const char* idMsg) {
  JsonDocument payload;
  payload["estado"] = orion::nomeEstado(estados.atual());
  payload["uptime_ms"] = millis();
  payload["em_movimento"] = motores.emMovimento();
  payload["imu_conectado"] = imu.conectado();
  payload["imu_calibrado"] = imu.calibrado();
  orion::enviarMensagem(Serial, "RESPONSE", origem, payload.as<JsonObjectConst>(), idMsg);
}

// Congela a orientacao atual como "nivelado" (grava na EEPROM). O robo
// precisa estar parado e nivelado na hora - ver imu_manager.h.
void responderCalibrarImu(const char* origem, const char* idMsg) {
  JsonDocument payload;
  bool ok = imu.calibrar();
  payload["ok"] = ok;
  payload["inclinacao_graus"] = imu.inclinacaoGraus();
  if (!ok) {
    payload["erro"] = imu.conectado() ? "leitura_invalida" : "imu_desconectado";
  }
  orion::enviarMensagem(Serial, "RESPONSE", origem, payload.as<JsonObjectConst>(), idMsg);
}

void tratarComando(JsonDocument& msg, const char* origem, const char* idMsg) {
  const char* comando = msg["payload"]["comando"] | "";

  if (strcmp(comando, "WHO_ARE_YOU") == 0) {
    responderWhoAreYou(origem, idMsg);
    return;
  }
  if (strcmp(comando, "RETURN_STATUS") == 0) {
    responderReturnStatus(origem, idMsg);
    return;
  }
  if (strcmp(comando, "SET_IMPACT_THRESHOLD") == 0) {
    JsonDocument payload;
    float limite = msg["payload"]["limite_g"] | 0.0f;
    bool ok = imu.definirLimiteImpacto(limite);
    payload["ok"] = ok;
    payload["limite_g"] = imu.limiteImpactoG();
    if (!ok) payload["erro"] = "fora_da_faixa_valida_1.05_a_7.5";
    orion::enviarMensagem(Serial, "RESPONSE", origem, payload.as<JsonObjectConst>(), idMsg);
    return;
  }
  if (strcmp(comando, "CALIBRATE_IMU") == 0) {
    responderCalibrarImu(origem, idMsg);
    return;
  }

  JsonObjectConst payload = msg["payload"];
  comandos.executar(comando, payload);
}

void processarQuadro(const uint8_t* dados, size_t tamanho) {
  JsonDocument msg;
  if (deserializeJson(msg, dados, tamanho) != DeserializationError::Ok) {
    return;  // JSON malformado - descarta silenciosamente (Cap 14 s.5)
  }

  const char* destino = msg["destino"] | "";
  if (strcmp(destino, orion::NOME_MODULO) != 0) {
    return;  // nao e para este modulo
  }

  const char* tipo = msg["tipo"] | "";
  const char* origem = msg["origem"] | "motion_core";
  const char* idMsg = msg["id"] | "";

  if (strcmp(tipo, "COMMAND") == 0) {
    ultimoComandoRecebido = millis();
    enviarPayloadVazio("ACK", origem, idMsg);
    tratarComando(msg, origem, idMsg);
  }
  // HEARTBEAT recebido do Raspberry: so precisa ter chegado, nada a responder.
}

}  // namespace

void setup() {
  Serial.begin(115200);

  motores.iniciar();
  encoders.iniciar();
  radar.iniciar();
  imu.iniciar();
  dht.iniciar();
  bateria.iniciar(pinos::BATERIA_SENSE);
  ultrassomTraseiro.iniciar(pinos::ULTRASSOM_TRAS_TRIG, pinos::ULTRASSOM_TRAS_ECHO);
  comandos.iniciar();
  g_payloadVazio.to<JsonObject>();  // forca virar {} em vez de null ao serializar

  estados.transicionarPara(orion::Estado::READY);
  estados.transicionarPara(orion::Estado::IDLE);
  ultimoComandoRecebido = millis();
}

void loop() {
  while (Serial.available() > 0) {
    uint8_t byte = Serial.read();
    if (decodificador.alimentar(byte)) {
      processarQuadro(decodificador.buffer(), decodificador.tamanho());
    }
  }

  motores.atualizar();
  radar.atualizar();
  ultrassomTraseiro.atualizar();
  dht.atualizarSeParado(!motores.emMovimento());
  bateria.atualizar();

  unsigned long agora = millis();

  if (agora - ultimoImu >= INTERVALO_IMU_MS) {
    ultimoImu = agora;
    imu.atualizar();
  }

  bool acionouParadaAgora = safety.avaliar(motores, radar, imu, ultimoComandoRecebido);
  if (acionouParadaAgora) {
    bool ehObstaculo = strcmp(safety.motivo(), "obstaculo_frontal") == 0;
    estados.transicionarPara(ehObstaculo ? orion::Estado::OBSTACLE_DETECTED
                                          : orion::Estado::SAFE_MODE);
  } else if (!safety.pararAtivo() && !motores.emMovimento() &&
             (estados.atual() == orion::Estado::OBSTACLE_DETECTED ||
              estados.atual() == orion::Estado::EXECUTING_MISSION)) {
    estados.transicionarPara(orion::Estado::IDLE);
  }

  notificarTransicaoDeEstado();
  notificarFimDeVarredura();

  if (agora - ultimoHeartbeat >= INTERVALO_HEARTBEAT_MS) {
    ultimoHeartbeat = agora;
    enviarPayloadVazio("HEARTBEAT", "motion_core");
  }

  if (agora - ultimaTelemetria >= INTERVALO_TELEMETRIA_MS) {
    ultimaTelemetria = agora;
    JsonDocument payload;
    telemetria.preencherPayload(payload.to<JsonObject>());
    orion::enviarMensagem(Serial, "TELEMETRY", "motion_core", payload.as<JsonObjectConst>());
  }
}
