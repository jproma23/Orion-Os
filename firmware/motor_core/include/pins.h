// Atribuicao de pinos do Motor Core (EDR-0031).
//
// PLACA: ESP32-D0WD-V3 (DevKit 38 pinos) dedicado so ao motor - NAO e o
// mesmo ESP32 do hardware_core (sensores/servos). As mesmas tres regras do
// ESP32 do hardware_core valem aqui:
//
//   1. GPIO 6..11 sao a memoria flash. Nunca usar.
//   2. GPIO 34..39 SO LEEM (input-only, sem pull-up interno) - por isso os
//      4 canais de encoder (input-only por natureza) moram todos aqui.
//   3. GPIO 0, 2, 12, 15 sao strapping - podem impedir o boot se estiverem
//      no nivel logico errado no instante do reset. Fora do mapa.
//
// Esta placa NUNCA liga WiFi (EDR-0031: UART cabeada, nunca WiFi, no laco
// de controle) - os pinos de ADC2 (25..27, compartilhados com o radio) nao
// tem nenhuma restricao aqui por causa disso.
#pragma once

namespace pinos {

// --- Encoders de quadratura (EDR-0031: 34, 35, 36, 39 - input-only) ---
// Motor esquerdo
constexpr int ENC_ESQ_A = 34;
constexpr int ENC_ESQ_B = 35;
// Motor direito
constexpr int ENC_DIR_A = 36;
constexpr int ENC_DIR_B = 39;

// --- Ponte H L298N (EDR-0031: driver escolhido pelo dono, 2026-09-13) ---
// IN1-IN4 controlam o sentido de cada motor; ENA/ENB recebem o PWM de
// velocidade. Nenhum destes pinos e strapping nem flash.
constexpr int MOTOR_ESQ_IN1 = 16;
constexpr int MOTOR_ESQ_IN2 = 17;
constexpr int MOTOR_DIR_IN3 = 18;
constexpr int MOTOR_DIR_IN4 = 19;
constexpr int MOTOR_ESQ_ENA = 25;  // PWM (LEDC) - velocidade motor esquerdo
constexpr int MOTOR_DIR_ENB = 26;  // PWM (LEDC) - velocidade motor direito

}  // namespace pinos
