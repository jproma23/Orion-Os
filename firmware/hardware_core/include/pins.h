// Atribuicao de pinos do Hardware Core (Cap 10).
//
// Pinos MARCADOS COMO CONFIRMADOS vem do guia de ligacao eletrica real desta
// montagem (Sentinela X - mesmo Mega fisico) e ja foram testados com
// hardware de verdade. Pinos MARCADOS COMO RESERVADO ainda nao tem nada
// fisicamente ligado nesta montagem (confirmado com o usuario em
// 2026-07-17) - o firmware deve funcionar e compilar normalmente mesmo sem
// esses perifericos conectados (leitura vazia/zerada, nunca crash).
#pragma once

#include <Arduino.h>

namespace pinos {

// --- Motores (CONFIRMADO) - 2x TB6600 -> 2x NEMA17 ---
constexpr uint8_t STEP_ESQUERDO = 2;
constexpr uint8_t DIR_ESQUERDO = 3;
constexpr uint8_t STEP_DIREITO = 4;
constexpr uint8_t DIR_DIREITO = 5;
constexpr uint8_t ENABLE_MOTORES = 6;  // compartilhado pelos dois drivers

// --- Ultrassonico frontal (CONFIRMADO) - HC-SR04 fixo, sem servo ---
constexpr uint8_t ULTRASSOM_FRENTE_TRIG = 22;
constexpr uint8_t ULTRASSOM_FRENTE_ECHO = 23;

// --- IMU (CONFIRMADO) - MPU6050/9250, pinos fixos de I2C do Mega ---
constexpr uint8_t IMU_SDA = 20;
constexpr uint8_t IMU_SCL = 21;

// --- Temperatura/umidade (CONFIRMADO) - DHT11/22 ---
constexpr uint8_t DHT_DATA = 24;

// --- RESERVADO: nao fisicamente conectado nesta montagem ---
constexpr uint8_t ENCODER_ESQUERDO = 18;  // interrupt externo do Mega
constexpr uint8_t ENCODER_DIREITO = 19;   // interrupt externo do Mega
constexpr uint8_t ULTRASSOM_TRAS_TRIG = 26;
constexpr uint8_t ULTRASSOM_TRAS_ECHO = 27;
constexpr uint8_t SERVO_RADAR = 9;   // varredura do ultrassom frontal (0-180)
constexpr uint8_t SERVO_PAN = 10;
constexpr uint8_t SERVO_TILT = 11;
constexpr uint8_t LED_LANTERNA = 25;

}  // namespace pinos
