/**
 * ORION - Motor Core (EDR-0031)
 *
 * ESP32 dedicado exclusivamente ao motor: dono da fronteira cmd_vel <-> odom
 * que o no-ponte ROS2 espera do outro lado. Separado do ESP32 de sensores
 * (firmware/hardware_core, que continua como esta - ToF, servos, WebSocket).
 *
 * SEM HARDWARE DE BANCADA AINDA (2026-09-13): motor JGB37-520 + encoder nao
 * chegaram. Este firmware compila mas NAO FOI TESTADO em roda real. Nao
 * declarar o Gate 1 fechado sem rodar de verdade com as rodas no ar
 * primeiro (RETOMADA_MIGRACAO.md / EDR-0031).
 */
#include <Arduino.h>
#include <ArduinoJson.h>
#include <string.h>

#include "encoder_pcnt.h"
#include "framing.h"
#include "motor_driver.h"
#include "pins.h"
#include "velocity_pid.h"

using namespace orion;

namespace {

// --- Dados ainda nao confirmados na bancada (RETOMADA_MIGRACAO.md) ---
// Reducao do JGB37-520: DECISAO PROVISORIA 1:90 (peca ainda a caminho).
constexpr float REDUCAO_ENGRENAGEM = 90.0f;
// PPR do encoder no eixo do MOTOR (antes da reducao): NAO CONFIRMADO. 11 e
// o valor mais comum nesta familia de motor (JGB37-520 com encoder magnetico
// de 2 canais), mas isto E UM CHUTE ate medir o encoder real na chegada -
// girar o eixo 1 volta a mao e contar os pulsos num canal.
constexpr float ENCODER_PPR_MOTOR_CHUTE = 11.0f;
// PCNT decodifica em 4x (ver encoder_pcnt.cpp) - cada ciclo eletrico do
// encoder vira 4 ticks contados.
constexpr float TICKS_POR_VOLTA_RODA =
    ENCODER_PPR_MOTOR_CHUTE * 4.0f * REDUCAO_ENGRENAGEM;
constexpr float TICKS_PARA_RADIANOS = (2.0f * PI) / TICKS_POR_VOLTA_RODA;

// EDR-0031: sem comando novo em 200ms, motores param - autonomo, nunca
// desabilitado nem para debug.
constexpr uint32_t WATCHDOG_MS = 200;
// EDR-0031: ESP32 -> Pi a ~50Hz.
constexpr uint32_t PERIODO_RELATORIO_MS = 20;
// Laco de controle do PID mais rapido que o relatorio - velocidade medida
// mais suave, menos ruido de quantizacao de ticks por amostra.
constexpr uint32_t PERIODO_CONTROLE_MS = 10;

EncoderPcnt g_encoder_esq;
EncoderPcnt g_encoder_dir;
MotorDriver g_motor_esq;
MotorDriver g_motor_dir;
VelocidadePid g_pid_esq;
VelocidadePid g_pid_dir;

DecodificadorQuadro g_decodificador;

volatile float g_alvo_esq_rad_s = 0.0f;
volatile float g_alvo_dir_rad_s = 0.0f;
volatile uint32_t g_ultimo_comando_ms = 0;

int64_t g_ticks_esq_anterior = 0;
int64_t g_ticks_dir_anterior = 0;
uint32_t g_ultimo_controle_ms = 0;
uint32_t g_ultimo_relatorio_ms = 0;

// {"tipo":"cmd_vel","rad_s_esq":X,"rad_s_dir":Y} - unico comando que este
// ESP32 entende (EDR-0031: contrato minimo, nao o protocolo rico do
// hardware_core).
void processarComando(const uint8_t* dados, size_t tamanho) {
  JsonDocument doc;
  DeserializationError erro = deserializeJson(doc, dados, tamanho);
  if (erro) {
    return;  // quadro corrompeu na desserializacao - descarta, nao trava
  }
  const char* tipo = doc["tipo"] | "";
  if (strcmp(tipo, "cmd_vel") != 0) {
    return;
  }
  g_alvo_esq_rad_s = doc["rad_s_esq"] | 0.0f;
  g_alvo_dir_rad_s = doc["rad_s_dir"] | 0.0f;
  g_ultimo_comando_ms = millis();
}

void lerSerial() {
  while (Serial.available() > 0) {
    if (g_decodificador.alimentar(static_cast<uint8_t>(Serial.read()))) {
      processarComando(g_decodificador.buffer(), g_decodificador.tamanho());
    }
  }
}

// EDR-0031: watchdog autonomo, nunca desabilitado. Roda todo ciclo do
// laco principal, independente de ter chegado comando novo ou nao.
bool watchdogDisparado() {
  return (millis() - g_ultimo_comando_ms) > WATCHDOG_MS;
}

void controlarMotores(float dt_s) {
  if (watchdogDisparado()) {
    g_motor_esq.parar();
    g_motor_dir.parar();
    g_pid_esq.zerar();
    g_pid_dir.zerar();
    return;
  }

  int64_t ticks_esq = g_encoder_esq.ticks();
  int64_t ticks_dir = g_encoder_dir.ticks();

  float rad_s_esq = (ticks_esq - g_ticks_esq_anterior) * TICKS_PARA_RADIANOS / dt_s;
  float rad_s_dir = (ticks_dir - g_ticks_dir_anterior) * TICKS_PARA_RADIANOS / dt_s;
  g_ticks_esq_anterior = ticks_esq;
  g_ticks_dir_anterior = ticks_dir;

  float saida_esq = g_pid_esq.atualizar(g_alvo_esq_rad_s, rad_s_esq, dt_s);
  float saida_dir = g_pid_dir.atualizar(g_alvo_dir_rad_s, rad_s_dir, dt_s);

  g_motor_esq.definir(saida_esq);
  g_motor_dir.definir(saida_dir);
}

// ESP32 -> Pi: contagem de ticks (cumulativa) por roda + timestamp do
// proprio ESP32 (EDR-0031: a integracao de odometria precisa do instante
// real da amostra, nao do instante de chegada na UART).
void enviarOdometria() {
  JsonDocument doc;
  doc["tipo"] = "odom";
  doc["ticks_esq"] = g_encoder_esq.ticks();
  doc["ticks_dir"] = g_encoder_dir.ticks();
  doc["t_us"] = micros();

  char buf[128];
  size_t tamanho = serializeJson(doc, buf, sizeof(buf));
  enviarQuadro(Serial, reinterpret_cast<const uint8_t*>(buf), tamanho);
}

}  // namespace

void setup() {
  Serial.begin(250000);
  Serial.setRxBufferSize(1024);  // mesma protecao do hardware_core

  g_encoder_esq.iniciar(0, pinos::ENC_ESQ_A, pinos::ENC_ESQ_B);
  g_encoder_dir.iniciar(1, pinos::ENC_DIR_A, pinos::ENC_DIR_B);

  g_motor_esq.iniciar(pinos::MOTOR_ESQ_IN1, pinos::MOTOR_ESQ_IN2, pinos::MOTOR_ESQ_ENA, 0);
  g_motor_dir.iniciar(pinos::MOTOR_DIR_IN3, pinos::MOTOR_DIR_IN4, pinos::MOTOR_DIR_ENB, 1);

  // Watchdog comeca "disparado": sem comando nenhum recebido ainda, os
  // motores devem ficar parados ate o Pi mandar o primeiro cmd_vel.
  g_ultimo_comando_ms = millis() - WATCHDOG_MS - 1;

  g_ultimo_controle_ms = millis();
  g_ultimo_relatorio_ms = millis();
}

void loop() {
  lerSerial();

  uint32_t agora = millis();

  if (agora - g_ultimo_controle_ms >= PERIODO_CONTROLE_MS) {
    float dt_s = (agora - g_ultimo_controle_ms) / 1000.0f;
    g_ultimo_controle_ms = agora;
    controlarMotores(dt_s);
  }

  if (agora - g_ultimo_relatorio_ms >= PERIODO_RELATORIO_MS) {
    g_ultimo_relatorio_ms = agora;
    enviarOdometria();
  }
}
