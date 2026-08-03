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
//   GPIO 15 - idem, com o log de boot.
//   Deixar os dois livres e mais barato que descobrir isso com o robo
//   montado. Se um dia faltar pino, tirar daqui e o ULTIMO recurso.
//
// GPIO 1 e 3 sao a UART0 (USB) e pertencem ao protocolo com o Raspberry.
// Nunca reaproveitar.
// ------------------------------------------------------------------------
#pragma once

#include <Arduino.h>

namespace pinos {

// --- Motores (CONFIRMADO 2026-07-19 no Mega, girando em bancada) - 2x TB6600
// -> 2x NEMA17. Ligacao real: PUL+/DIR+ nos pinos abaixo e PUL-/DIR- no GND
// da placa (retorno do optoacoplador - sem ele o driver nao ve pulso
// nenhum). Fonte 12-24V dos motores e isolada pelo opto: nao precisa de
// GND comum com o microcontrolador.
//
// RISCO ABERTO NA MIGRACAO (2026-08-02): a entrada opto do TB6600 foi
// dimensionada para logica de 5V. O ESP32 entrega 3,3V, o que derruba a
// corrente no LED do opto de ~14mA para ~8mA. Pode ser suficiente ou pode
// nao disparar - so a bancada responde. Se nao disparar, a correcao e um
// level shifter na linha de PUL/DIR, nao mexer no firmware.
constexpr uint8_t STEP_ESQUERDO = 25;  // (Mega: 2)
constexpr uint8_t DIR_ESQUERDO = 26;   // (Mega: 3)
constexpr uint8_t STEP_DIREITO = 27;   // (Mega: 4)
constexpr uint8_t DIR_DIREITO = 14;    // (Mega: 5)
// ENA dos TB6600 esta SOLTO por decisao do usuario (2026-07-19) = drivers
// sempre habilitados, como na CNC dele. O firmware ainda aciona este pino,
// mas sem efeito fisico nesta montagem.
constexpr uint8_t ENABLE_MOTORES = 13;  // (Mega: 6) compartilhado; NAO CONECTADO

// --- Ultrassonico frontal - HC-SR04 fixo, sem servo ---
// CORRIGIDO 2026-07-20, VALIDADO NA BANCADA 2026-07-26: frente e tras
// estavam trocados no codigo. Duas evidencias independentes: tapar o sensor
// traseiro mexia na distancia_frontal_cm (a navegacao desviava olhando o
// sensor errado); e, com os DOIS sensores lendo ao mesmo tempo, um obstaculo
// posto a 30cm ATRAS do robo aparecia em distancia_frontal_cm (leitura
// estavel de 22,9cm) enquanto o ambiente aberto da frente aparecia em
// distancia_traseira_cm (~178cm, oscilando).
//
// ATENCAO A FIACAO (achado de 2026-07-26): nesta montagem os fios estavam
// com TRIG e ECHO invertidos nos DOIS modulos - a placa pulsava no pino de
// ECHO do sensor (saida contra saida) e escutava no TRIG. Resultado: os
// dois sensores mudos, ECHO nunca subia, e a seguranca travava o robo em
// OBSTACLE_DETECTED. Corrigido nos FIOS pelo usuario, nao no codigo.
//
// *** NAO LIGAR NO ESP32 SEM DIVISOR (2026-08-02) ***
// O ECHO do HC-SR04 entrega 5V. O GPIO do ESP32 aguenta 3,3V. Ligar direto
// QUEIMA a placa - mesma familia de erro do LiDAR STL-50B2 e dos sensores
// Cotrinex. Exige divisor resistivo (ex.: 1k/2k) ou level shifter.
// Decisao do dono (2026-07-30): quando o LiDAR ToF entrar, os ultrassons
// SAEM. Ate la os dois ficam DESPLUGADOS - nao vale gastar componente e
// bancada numa peca que ja tem data de saida.
// Os ECHO foram para pinos que SO LEEM (34/35) de proposito: assim um erro
// futuro de codigo nao consegue nem tentar dirigir a linha contra o sensor,
// que foi exatamente a falha de 2026-07-26.
constexpr uint8_t ULTRASSOM_FRENTE_TRIG = 32;  // (Mega: 26)
constexpr uint8_t ULTRASSOM_FRENTE_ECHO = 34;  // (Mega: 27) SO LEITURA

// --- Ultrassonico traseiro - HC-SR04 fixo (mesmas ressalvas do frontal) ---
constexpr uint8_t ULTRASSOM_TRAS_TRIG = 33;  // (Mega: 22)
constexpr uint8_t ULTRASSOM_TRAS_ECHO = 35;  // (Mega: 23) SO LEITURA

// --- I2C: IMU (MPU6050/9250) + bussola (QMC6310) ---
// No Mega o I2C morava em pinos fixos (20/21) e o Wire.begin() nao levava
// argumento. No ESP32 QUALQUER par serve, mas justamente por isso o
// Wire.begin() TEM que receber os pinos - sem isso ele assume o padrao e o
// barramento fica mudo sem erro nenhum.
constexpr uint8_t IMU_SDA = 21;  // (Mega: 20)
constexpr uint8_t IMU_SCL = 22;  // (Mega: 21)

// --- Temperatura/umidade (CONFIRMADO) - DHT11/22 ---
// O DHT22 opera de 3 a 5V, entao atravessa a migracao sem adaptacao. O
// DHT11 e mais sensivel: se vier lixo, alimentar o modulo com 3,3V (e nao
// com 5V) resolve, porque ai o pull-up da linha de dados tambem cai.
constexpr uint8_t DHT_DATA = 4;  // (Mega: 24)

// --- Servo do radar (CONFIRMADO 2026-07-18) - varre o ultrassom frontal ---
// Servos aceitam sinal de 3,3V sem adaptacao; a alimentacao continua em 5V.
constexpr uint8_t SERVO_RADAR = 18;  // (Mega: 9)

// --- Servos pan/tilt ---
// Os pinos estavam certos, mas os fios de SINAL estavam cruzados na montagem:
// ate 2026-07-20 o pino do tilt acionava o servo horizontal e vice-versa, o
// que so aparece movendo UM eixo isolado (teste em tools/testar_so_pan.py).
// Corrigido invertendo os dois fios de sinal (nao o firmware). Agora bate:
// SERVO_PAN = horizontal, SERVO_TILT = vertical. VALIDADO 2026-07-20.
constexpr uint8_t SERVO_PAN = 19;   // (Mega: 10)
constexpr uint8_t SERVO_TILT = 23;  // (Mega: 11)

// --- Reles de energia (CONFIRMADO 2026-07-23) - cortam alimentacao de
// verdade, ao contrario do ENABLE_MOTORES acima (so logica, e nem esta
// fisicamente ligado nesta montagem). Ambos os modulos de rele usados sao
// ativos em LOW (testado pelo usuario).
//
// RELE_MOTORES: alimenta os TB6600. Comeca e fica em HIGH (motores SEM
// energia) sempre que parado - so vai a LOW enquanto o robo anda de
// verdade (Cap 18: em duvida, motores desligados).
//
// A ESCOLHA DO GPIO 5 NAO E ARBITRARIA. No Mega este rele ja precisou fugir
// do pino 13 (2026-07-24) porque era o LED embutido, que o bootloader pisca
// em TODO reset - antes do setup() rodar - e o rele "tremelicava" fora do
// controle do codigo. O ESP32 tem a mesma classe de problema, so que pior:
// varios GPIOs nascem em LOW, e LOW aqui significa MOTOR COM ENERGIA. O
// GPIO 5 nasce em HIGH (tem pull-up interno no boot), ou seja, nasce no
// estado seguro. O fail-safe passa a estar no silicio, nao na promessa de
// que o setup() roda rapido.
constexpr uint8_t RELE_MOTORES = 5;  // (Mega: 12, antes 13)
// RELE_VENTILADOR: alimenta o cooler do Raspberry. Comeca em LOW
// (ventilador LIGADO - fail-safe, resfria por padrao) e so desliga (HIGH)
// quando o Raspberry confirmar temperatura confortavel via FAN_OFF.
// Fisicamente DESCONECTADO desde 2026-07-24 por consumo de energia;
// FAN_ON/FAN_OFF continuam existindo no protocolo, sem efeito fisico ate
// alguem reconectar.
constexpr uint8_t RELE_VENTILADOR = 17;  // (Mega: 7)

// --- Tensao da bateria (RESERVADO - divisor ainda nao montado) ---
// Pack de parafusadeira 18V. Ver bateria_manager.h para o esquema de
// ligacao e a exigencia de GND comum.
//
// *** O DIVISOR ANOTADO PARA O MEGA NAO SERVE MAIS (2026-08-02) ***
// 100k/27k foi dimensionado para um ADC de 5V: com 18V no pack a saida e
// 18 * 27/127 = 3,83V. O ADC do ESP32 satura em 3,3V e o pino se danifica
// acima disso. Como o divisor NUNCA foi montado, nao houve dano - mas os
// valores antigos viraram errados e precisam ser recalculados antes de
// soldar qualquer coisa. Reserve margem para o pack CHEIO (21V em 5S), nao
// para os 18V nominais.
//
// GPIO 36 e ADC1: continua lendo com o WiFi ligado (regra 3 no topo).
constexpr uint8_t BATERIA_SENSE = 36;  // (Mega: A0)

// --- RESERVADO: nao fisicamente conectado nesta montagem ---
// No Mega os encoders eram obrigados a viver nos pinos 18/19, os unicos com
// interrupcao externa sobrando. No ESP32 QUALQUER GPIO interrompe, entao a
// escolha aqui foi por outro criterio: ENCODER_DIREITO ficou num pino comum
// (16) e nao num 34..39 porque aqueles nao tem pull-up interno - se o
// encoder for de coletor aberto, o 16 funciona sozinho e o 39 exigiria um
// resistor externo que hoje nao existe na bancada.
constexpr uint8_t ENCODER_ESQUERDO = 39;  // (Mega: 18) SO LEITURA
constexpr uint8_t ENCODER_DIREITO = 16;   // (Mega: 19)
constexpr uint8_t LED_LANTERNA = 2;       // (Mega: 25) LED da propria placa

}  // namespace pinos
