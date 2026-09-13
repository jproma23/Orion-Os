// Atribuicao de pinos do Hardware Core (Cap 10).
//
// PLACA: ESP32-D0WD-V3 (DevKit 38 pinos), desde 2026-08-02. O Arduino Mega
// que ocupava este posto MORREU; os numeros de pino dele estao preservados
// nos comentarios abaixo como "(Mega: N)" porque as notas de fiacao que
// vieram junto custaram bancada e continuam valendo - o que mudou foi o
// numero do pino, nao a ligacao fisica do periferico.
//
// Pinos MARCADOS COMO CONFIRMADOS vem do guia de ligacao eletrica real desta
// montagem e ja foram testados com hardware de verdade NO MEGA - a
// revalidacao no ESP32 ainda nao aconteceu. Pinos MARCADOS COMO RESERVADO
// ainda nao tem nada fisicamente ligado - o firmware deve funcionar e
// compilar normalmente mesmo sem esses perifericos conectados (leitura
// vazia/zerada, nunca crash).
//
// ------------------------------------------------------------------------
// AS TRES REGRAS DO ESP32 QUE ESTE MAPA OBEDECE (nao mexer sem reler):
//
//   1. GPIO 6..11 sao a memoria flash. Usar qualquer um deles trava a placa
//      no boot. Nao aparecem aqui e nao podem aparecer.
//   2. GPIO 34..39 SO LEEM. Nao tem saida e nao tem pull-up interno. Servem
//      para ECHO, encoder e bateria; nunca para TRIG, servo ou rele.
//   3. O ADC2 (GPIO 0,2,4,12..15,25..27) PARA DE FUNCIONAR quando o WiFi
//      liga. Toda leitura analogica tem que cair no ADC1 (GPIO 32..39).
//      Por isso BATERIA_SENSE = 36 e nao um pino qualquer.
//
// E os dois pinos que ficaram DE PROPOSITO vazios:
//   GPIO 12 - se estiver em HIGH no instante do boot, o ESP32 escolhe a
//             tensao errada para a flash e a placa nao sobe.
//   GPIO 15 - idem, com o log de boot. LIVRE OUTRA VEZ (ver secao de
//             motor abaixo) - usado como PWM de UM lado do L298N. So
//             importa o nivel de boot se algo mais puxar o pino durante o
//             reset; um ENB de ponte H nao faz isso sozinho, mas fica
//             registrado o risco.
//   Deixar os dois livres e mais barato que descobrir isso com o robo
//   montado. Se um dia faltar pino, tirar daqui e o ULTIMO recurso.
//
// GPIO 1 e 3 sao a UART0 (USB) e pertencem ao protocolo com o Raspberry.
// Nunca reaproveitar.
// ------------------------------------------------------------------------
#pragma once

#include <Arduino.h>

namespace pinos {

// --- Motor DC JGB37-520 + encoder, via L298N (EDR-0031, revisado
// 2026-09-13: consolidado NESTE ESP32 - decisao do dono, era pra ser um
// ESP32 dedicado separado). SUBSTITUI os 2x TB6600 -> 2x NEMA17 que
// ocupavam este lugar.
//
// Pinos REAPROVEITADOS dos steppers aposentados: os 4 de STEP/DIR viram
// IN1-IN4 do L298N, e o ENABLE_MOTORES (que nem estava fisicamente ligado)
// vira um dos PWM. O outro PWM usa o GPIO que tinha ficado livre com o
// cancelamento do encoder antigo (ver historico abaixo).
constexpr uint8_t MOTOR_ESQ_IN1 = 27;  // era STEP_ESQUERDO
constexpr uint8_t MOTOR_ESQ_IN2 = 14;  // era DIR_ESQUERDO
constexpr uint8_t MOTOR_DIR_IN3 = 25;  // era STEP_DIREITO
constexpr uint8_t MOTOR_DIR_IN4 = 26;  // era DIR_DIREITO
constexpr uint8_t MOTOR_ESQ_PWM = 13;  // era ENABLE_MOTORES (nunca ligado antes)
constexpr uint8_t MOTOR_DIR_PWM = 15;  // livre desde o cancelamento do encoder antigo (2026-08-17)

// MOTORES ESPELHADOS NA MONTAGEM (medido na bancada em 2026-08-05, com os
// steppers - a mesma montagem fisica de rodas continua): os dois motores se
// olham, entao o MESMO sentido de giro fisico corresponde a lados opostos
// do H-bridge. Fica na fiacao/config, nao na logica de movimento.
constexpr bool DIR_INVERTIDO_ESQUERDO = false;
constexpr bool DIR_INVERTIDO_DIREITO = true;

// --- Encoders do motor DC - VOLTOU em 2026-09-13, CANAL UNICO ---
//
// Historico: a instalacao de um sensor indutivo NPN foi CANCELADA em
// 2026-08-17 (o motor era stepper, odometria por passos comandados +
// bussola + LiDAR bastava). O motor mudou para DC com encoder embutido
// (JGB37-520) - agora o encoder e a UNICA fonte de velocidade real pro PID,
// entao ele volta a ser necessario.
//
// SO 1 CANAL POR RODA, DE PROPOSITO: os pinos ECHO dos ultrassons (34/35)
// pareciam livres para um segundo canal (quadratura completa), mas o ECHO
// frontal (GPIO34) alimenta o SafetyManager de verdade (parada por
// obstaculo) - nao vale a pena arriscar seguranca por resolucao de
// encoder. Os UNICOS pinos input-only totalmente livres nesta montagem sao
// GPIO 36 (era BATERIA_SENSE, divisor nunca chegou a ser montado) e GPIO 39
// (nunca teve nada). NENHUM ultrassom foi tocado - os dois continuam
// exatamente como estavam.
constexpr uint8_t ENCODER_ESQ = 39;  // livre
constexpr uint8_t ENCODER_DIR = 36;  // era BATERIA_SENSE

// --- Ultrassonico frontal - HC-SR04 fixo, sem servo (INTOCADO) ---
// Alimenta o SafetyManager (parada por obstaculo) - ver motor_manager.h e
// a nota de encoder acima sobre por que estes pinos NAO foram realocados.
constexpr uint8_t ULTRASSOM_FRENTE_TRIG = 32;  // (Mega: 26)
constexpr uint8_t ULTRASSOM_FRENTE_ECHO = 34;  // (Mega: 27) SO LEITURA - seguranca real

// --- Ultrassonico traseiro - HC-SR04 fixo (INTOCADO, so telemetria) ---
constexpr uint8_t ULTRASSOM_TRAS_TRIG = 33;  // (Mega: 22)
constexpr uint8_t ULTRASSOM_TRAS_ECHO = 35;  // (Mega: 23) SO LEITURA

// --- I2C: bussola (HMC5883L/QMC5883L) ---
// A MPU6050 (IMU) foi DESCARTADA em 2026-08-18 e saiu desta placa; o unico
// dispositivo I2C que resta e a bussola. A bussola QMC6310 (endereco 0x1C)
// foi substituida em 2026-08-15 por uma GY-271 vendida como HMC5883L - que
// por dentro pode ser HMC real (0x1E, identidade "H43") ou o clone QMC5883L
// (0x0D). O bussola_manager.h faz AUTODETECCAO dos dois. Fiacao do modulo
// GY-271: VCC (vermelho)=3V3, GND (preto)=GND, SCL (verde)=22, SDA
// (branco)=21.
constexpr uint8_t BUSSOLA_SDA = 21;  // (Mega: 20)
constexpr uint8_t BUSSOLA_SCL = 22;  // (Mega: 21)

// --- Temperatura/umidade (CONFIRMADO) - DHT11/22 ---
constexpr uint8_t DHT_DATA = 4;  // (Mega: 24)

// --- Servo do radar (CONFIRMADO 2026-07-18) - varre o ultrassom frontal ---
constexpr uint8_t SERVO_RADAR = 18;  // (Mega: 9)

// --- Servos pan/tilt ---
// SERVO_PAN = horizontal, SERVO_TILT = vertical. VALIDADO 2026-07-20.
constexpr uint8_t SERVO_PAN = 19;   // (Mega: 10)
constexpr uint8_t SERVO_TILT = 23;  // (Mega: 11)

// --- Reles de energia (CONFIRMADO 2026-07-23) ---
// RELE_MOTORES: agora alimenta o L298N (antes, os TB6600). Mesma logica de
// fail-safe: HIGH = motores SEM energia, LOW so enquanto anda de verdade.
constexpr uint8_t RELE_MOTORES = 5;  // (Mega: 12, antes 13)
// RELE_VENTILADOR: fisicamente DESCONECTADO desde 2026-07-24.
constexpr uint8_t RELE_VENTILADOR = 17;  // (Mega: 7)

// --- Tensao da bateria (RESERVADO - divisor ainda nao montado, e o pino
// que ele usaria foi realocado pro encoder acima) ---
// Pack de parafusadeira 18V. O GPIO 36 que estava reservado pra isto virou
// ENCODER_DIR (nada fisico foi perdido: o divisor nunca chegou a ser
// montado). Antes de montar o sensor de bateria, escolher outro pino ADC1
// livre e recalcular o divisor para a faixa 0-3,3V do ESP32 (NAO usar os
// valores antigos de 100k/27k, dimensionados pra um ADC de 5V).

// --- Lanterna: rele (desde 2026-08-04) ---
// Modulo de rele ATIVO EM LOW, igual aos outros dois desta montagem: LOW
// fecha o contato e ACENDE, HIGH abre e apaga.
//
// ATENCAO AO ESTADO DE BOOT: o GPIO 2 do ESP32 e strapping pin e nasce em
// LOW (pull-down interno), ou seja, nasce ACESO para um rele ativo em LOW.
// O `digitalWrite(HIGH)` antes do `pinMode(OUTPUT)` encurta essa janela ao
// minimo, mas nao a elimina.
constexpr uint8_t RELE_LANTERNA = 2;  // (Mega: 25) era LED_LANTERNA

//: Niveis do modulo de rele da lanterna, nomeados para o codigo dizer o que
//: FAZ e nao o que escreve.
constexpr uint8_t LANTERNA_ACESA = LOW;
constexpr uint8_t LANTERNA_APAGADA = HIGH;

}  // namespace pinos
