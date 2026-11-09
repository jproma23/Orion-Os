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
// FIACAO REAL (dono, 2026-10-25): IN1..IN4 nos GPIO 32, 33, 25, 26. O
// mapa anterior (27/14/25/26 + PWM em 13/15) nunca chegou a ser a fiacao
// fisica. ENA/ENB NAO vem do ESP32: a velocidade e PWM nos proprios IN (o
// pino do sentido "ativo" recebe o PWM e o outro fica em LOW). Os GPIO 13 e
// 15 continuam declarados e ficam em HIGH o tempo todo: se o ENA/ENB estiver
// com jumper, nao fazem nada; se um dia alguem passar fio do ENA/ENB para
// eles, o motor continua habilitado em vez de parar mudo.
//
// QUAL PAR E QUAL LADO ainda nao foi visto na bancada: 32/33 = esquerdo e
// 25/26 = direito segue o mapa antigo (25/26 ja eram do direito). Se a
// roda que gira for a outra, troca-se aqui, nao no fio.
constexpr uint8_t MOTOR_ESQ_IN1 = 32;  // era TRIG do ultrassom frontal
constexpr uint8_t MOTOR_ESQ_IN2 = 33;  // era TRIG do ultrassom traseiro
constexpr uint8_t MOTOR_DIR_IN3 = 25;
constexpr uint8_t MOTOR_DIR_IN4 = 26;
constexpr uint8_t MOTOR_ESQ_EN = 13;  // sempre HIGH - ver acima
constexpr uint8_t MOTOR_DIR_EN = 15;  // sempre HIGH - strapping, so pesa no reset

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
// SO 1 CANAL POR RODA - mas o motivo CAIU em 2026-09-19. A regra era: os
// pinos ECHO dos ultrassons (34/35) pareciam livres para um segundo canal
// (quadratura completa), so que o ECHO frontal (GPIO34) alimentava o
// SafetyManager de verdade, e nao valia arriscar seguranca por resolucao de
// encoder.
//
// Os ultrassons sairam do robo. GPIO 34 e 35 ficaram livres e, desde
// 2026-10-25, sao o encoder esquerdo (A e B). Sao
// input-only - exatamente o que um canal B de encoder precisa. Quem for
// atras de quadratura completa (a odometria hoje roda com um encoder so,
// decisao de custo de 13/08) comeca por aqui: os pinos existem e nao
// disputam mais nada.
// PLACA DO DONO NAO TEM GPIO 36 NEM 39 (dono, 2026-10-25) - o mapa antigo
// punha os encoders neles, ou seja, em pino nenhum. Fiacao real:
// esquerdo A=34, B=35; direito A=14, B=27. O firmware ainda le so o canal
// A; o B esta ligado e declarado para a quadratura futura.
// GPIO 34 nao tem pull-up interno: se o encoder esquerdo nao contar, falta
// um resistor de 10k do amarelo para o 3,3 V. O 14 tem (o encoder_pcnt liga).
// TROCADOS EM 2026-10-31, por MEDICAO: com a roda DIREITA (IN 25/26) girando,
// uma varredura de todos os pinos (TEST_WHEEL "varrer") viu so o 34 e o 35
// mudarem (1765 vezes em 1 s cada); com ela parada, nenhum. O dono confirmou:
// "motor direito 34 e 35". O mapa antigo lia a roda direita no 14 e a
// esquerda no 34 - cada roda olhava o encoder da OUTRA, e as duas davam 0.
// O 34/35 nao tem pull-up interno e nao precisou: o encoder dirige o fio.
constexpr uint8_t ENCODER_ESQ = 14;    // fio amarelo, esquerdo
constexpr uint8_t ENCODER_ESQ_B = 27;  // fio verde, esquerdo (ainda nao lido)
constexpr uint8_t ENCODER_DIR = 34;    // fio amarelo, direito
constexpr uint8_t ENCODER_DIR_B = 35;  // fio verde, direito (ainda nao lido)

// --- Ultrassonicos: REMOVIDOS DO ROBO em 2026-09-19 ---
// Eram dois HC-SR04 (frente em 32/34, tras em 33/35) e o servo do radar em
// 18. Sairam fisicamente, por decisao do dono: quem mede distancia agora e
// o LiDAR STL-50B2, ligado ao Raspberry, com 12 setores de 360 graus a
// 2,5 Hz - contra 7 angulos numa varredura de 2,1 segundos.
//
// O 18 esta LIVRE; 32/33 viraram IN1/IN2 e 34/35 o encoder esquerdo
// (2026-10-25). Ficam listados aqui como
// livres, e nao apagados em silencio, porque a proxima pessoa que precisar
// de um pino vai procurar nesta lista.

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

// --- Servos pan/tilt ---
// SERVO_PAN = horizontal, SERVO_TILT = vertical. VALIDADO 2026-07-20.
// TROCADOS em 2026-11-01: na montagem final o dono ligou o pan no 23 e o
// tilt no 19 (pedir pan=30 levantava a cabeca). Trocado aqui, nao nos fios.
constexpr uint8_t SERVO_PAN = 23;   // (Mega: 10)
constexpr uint8_t SERVO_TILT = 19;  // (Mega: 11)

// --- Reles de energia (CONFIRMADO 2026-07-23) ---
// RELE_MOTORES: agora alimenta o L298N (antes, os TB6600). Mesma logica de
// fail-safe: HIGH = motores SEM energia, LOW so enquanto anda de verdade.
//
// O RELE SAIU DO ROBO (dono, 2026-10-25): o L298N esta direto na fonte. O
// pino continua sendo escrito, mas nao corta nada - o motor so para porque
// IN1=IN2=LOW. Consequencia: NO BOOT E NO RESET do ESP32 os IN ficam
// flutuando por alguns milissegundos e a ponte esta energizada; a roda pode
// dar um tranco. Antes o rele cobria essa janela.
constexpr uint8_t RELE_MOTORES = 5;  // (Mega: 12, antes 13) - sem rele desde 25/10
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
