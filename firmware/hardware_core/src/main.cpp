// Hardware Core - firmware completo (Cap 10).
//
// PLACA: ESP32-D0WD-V3 desde 2026-08-02, no lugar do Arduino Mega que
// morreu. Os numeros de pino todos mudaram - a fiacao fisica nao. Ver
// include/pins.h, que guarda o de-para e o motivo de cada escolha.
//
// Serial (UART0, GPIO 1/3, USB) e EXCLUSIVA do protocolo com o Raspberry -
// nunca usar Serial.print() aqui para depuracao, corromperia o enquadramento
// binario.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <EEPROM.h>
#include <WiFi.h>
#include <Wire.h>
#include <esp_bt.h>
#include <driver/gpio.h>
#include <soc/gpio_periph.h>
#include <esp_task_wdt.h>
#include <string.h>

#include "bateria_manager.h"
#include "bussola_manager.h"
#include "command_executor.h"
#include "dht_manager.h"
#include "estado.h"
#include "framing.h"
#include "motor_manager.h"
#include "pins.h"
#include "protocolo.h"
#include "safety_manager.h"
#include "telemetry_manager.h"

// Os simbolos __heap_start/__brkval do avr-libc que moravam aqui foram
// REMOVIDOS na migracao para o ESP32 (2026-08-02): eles nao existem fora do
// AVR. Estavam declarados e nunca eram usados - a medicao de RAM livre que
// os justificava nao chegou a ser escrita. No ESP32 o equivalente e
// ESP.getFreeHeap(), que ja vem pronto e nao precisa de declaracao externa.

namespace {

orion::DecodificadorQuadro decodificador;
orion::MaquinaDeEstados estados;
orion::MotorManager motores;
orion::DhtManager dht;
orion::BateriaManager bateria;
orion::SafetyManager safety;
orion::BussolaManager bussola;
orion::CommandExecutor comandos(motores, estados, safety);
orion::TelemetryManager telemetria(motores, dht, estados,
                                  bateria, bussola, safety);

constexpr unsigned long INTERVALO_HEARTBEAT_MS = 1000;
// DOIS REGIMES DE TELEMETRIA (2026-07-27, pedido do usuario a partir da
// medicao): o pacote completo bloqueia a serial por 74 ms, e enquanto ela
// bloqueia o loop nao le o ultrassom. Parado isso e inofensivo; andando e
// perigoso, porque o eco perdido vira "caminho livre".
//
// Parado  -> pacote COMPLETO, devagar (1 s). Nada ali muda rapido.
// Andando -> pacote REDUZIDO, rapido (200 ms). So o que seguranca e
//            navegacao consomem, para o Pi receber dado fresco sem pagar
//            a cegueira longa.
constexpr unsigned long INTERVALO_TELEMETRIA_PARADO_MS = 1000;
constexpr unsigned long INTERVALO_TELEMETRIA_MOVENDO_MS = 200;

unsigned long ultimoHeartbeat = 0;
unsigned long ultimaTelemetria = 0;
//: millis() do ultimo comando de MOVIMENTO - o relogio do homem-morto. So
//: os comandos de orion::renovaHomemMorto() (command_executor.h) mexem
//: aqui; SET_PAN_TILT e companhia nao (2026-11-10, C1-05/C4-03).
unsigned long ultimoComandoRecebido = 0;
//: Ultimo HEARTBEAT que chegou do Raspberry. Ate 2026-11-10 ele era
//: recebido e ignorado; agora e o que mantem vivo um movimento com fim
//: (safety_manager.h). `heartbeatPiVisto` separa "nunca chegou" de "chegou
//: no millis() 0".
unsigned long ultimoHeartbeatPi = 0;
bool heartbeatPiVisto = false;
// O robo esta girando no lugar PARA calibrar a bussola (CALIBRATE_COMPASS
// com "girar": true). O loop() para as rodas quando a coleta acaba.
bool girandoParaCalibrar = false;

// Instrumentacao do tempo de volta do loop (2026-07-25). Motivo real: o
// ultrassom e lido por sondagem, e o eco de um obstaculo a 30 cm dura so
// 1,7 ms - se a volta do loop demorar mais que isso, o pulso comeca e
// termina entre duas leituras do pino e a distancia nunca fica valida.
// Medido ao vivo: zero leituras validas em 18 s com obstaculo a ~1 m.
// Estes numeros vao na telemetria para achar o culpado com dado, em vez
// de palpite (suspeito principal: o Serial.write da telemetria, que
// bloqueia quando o buffer de saida enche).
unsigned long loopAnteriorUs = 0;
unsigned long loopMaxUs = 0;
unsigned long loopSomaUs = 0;
unsigned long loopVoltas = 0;

// Instrumentacao dirigida (2026-07-27, segunda rodada). A primeira rodada
// (loop_max_us) provou que a volta do loop estoura, mas nao QUEM estoura: a
// troca de 115200 para 250000 baud derrubou o pico de 84ms para so 68ms,
// contra os ~24ms que a conta do tempo de serial previa. Ou seja, a serial
// e parcela e nao causa principal, e o resto seguia sem explicacao.
//
// Em vez de chutar de novo, cada suspeito passa a ser cronometrado
// separadamente. `t_envio_us` mede a telemetria ANTERIOR (nao da para um
// pacote informar o proprio tempo de envio); `t_bussola_us` acumula o que a
// bussola gastou desde o ultimo pacote (o IMU saiu em 2026-08-18 e levou o
// contador `t_imu_us` junto).
unsigned long tempoEnvioAnteriorUs = 0;
unsigned long tempoBussolaAcumuladoUs = 0;
uint16_t leiturasBussola = 0;
orion::Estado estadoAnterior = orion::Estado::BOOT;

JsonDocument g_payloadVazio;  // reutilizado para ACK/HEARTBEAT (sempre {})

// DIAGNOSTICO DE I2C (2026-07-27). Motivo: a MPU6050 e a bussola QMC6310
// dividiam os pinos 20/21 e as duas apareceram mudas na telemetria
// (`bussola_conectada` false). Sem enxergar o
// barramento, "o modulo esta quebrado", "o endereco e outro" e "o barramento
// nao sobe" sao indistinguiveis de fora - e cada um pede um conserto
// diferente. Uma varredura unica no boot resolve a duvida com dado. Hoje so
// a bussola resta no barramento (a MPU6050 saiu em 2026-08-18), mas a
// varredura continua valendo como diagnostico de presenca dela.
constexpr uint8_t I2C_MAX_ENCONTRADOS = 4;
uint8_t g_i2cEncontrados[I2C_MAX_ENCONTRADOS] = {0, 0, 0, 0};
uint8_t g_i2cTotal = 0;

void escanearI2c() {
  // Faixa valida de enderecos de 7 bits (0x00-0x07 e 0x78-0x7F sao
  // reservados pela especificacao do I2C). Um endereco "responde" quando o
  // dispositivo puxa o ACK - nada e lido nem escrito, entao a varredura nao
  // altera a configuracao de ninguem.
  for (uint8_t endereco = 0x08; endereco < 0x78; endereco++) {
    Wire.beginTransmission(endereco);
    if (Wire.endTransmission() == 0) {
      if (g_i2cTotal < I2C_MAX_ENCONTRADOS) {
        g_i2cEncontrados[g_i2cTotal] = endereco;
      }
      g_i2cTotal++;
    }
  }
}

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

// notificarFimDeVarredura() SAIU em 2026-09-19, com o radar. Publicava
// `motion.scan_complete` com as 7 leituras da varredura; o servo e o
// ultrassom foram retirados do robo, e quem varre agora e o LiDAR ligado ao
// Raspberry, que publica `lidar.scan` direto no barramento de la - sem
// passar por esta serial.

void responderWhoAreYou(const char* origem, const char* idMsg) {
  JsonDocument payload;
  payload["nome"] = orion::NOME_MODULO;
  payload["versao_modulo"] = orion::VERSAO_FIRMWARE;
  payload["versao_protocolo"] = orion::VERSAO_PROTOCOLO;
  payload["firmware_git"] = orion::COMMIT_FIRMWARE;  // C4-07, ver protocolo.h
  orion::enviarMensagem(Serial, "RESPONSE", origem, payload.as<JsonObjectConst>(), idMsg);
}

void responderReturnStatus(const char* origem, const char* idMsg) {
  JsonDocument payload;
  payload["estado"] = orion::nomeEstado(estados.atual());
  payload["uptime_ms"] = millis();
  payload["em_movimento"] = motores.emMovimento();
  payload["bussola_conectada"] = bussola.conectado();
  orion::enviarMensagem(Serial, "RESPONSE", origem, payload.as<JsonObjectConst>(), idMsg);
}

// Comeca a calibracao da bussola e responde NA HORA, sem esperar os 25 s de
// coleta. Tem que ser assim: o watchdog de 2 s reiniciaria o Mega se este
// handler ficasse esperando, e a serial ficaria muda enquanto isso. Quem
// pediu acompanha o fim pelos campos `bussola_calibrando`/`bussola_calibrada`
// da telemetria, que sai a cada 500 ms.
//
// Durante a coleta o robo precisa GIRAR para todos os lados. Com "girar":
// true (2026-10-31, pedido do dono: "ensine o robo a calibrar sua bussola
// atraves de movimentos das rodas") quem gira e ele mesmo: devagar, no
// proprio eixo, e PARA SOZINHO quando a coleta acaba. A decisao de girar
// continua sendo do Raspberry (regra 3) - e ele quem manda o "girar"; o
// firmware so nao precisa de ninguem para lembrar de parar.
void responderCalibrarBussola(JsonObjectConst p, const char* origem, const char* idMsg) {
  JsonDocument payload;
  bool girar = p["girar"] | false;
  bool ok = bussola.iniciarCalibracao(girar);
  if (ok && girar) {
    // Devagar: a bussola le a 10 Hz, e em 25 s a 30% o robo da varias
    // voltas - amostras de todos os lados, sem borrar.
    motores.girarContinuo(true, p["velocidade_percent"] | 30.0f);
    girandoParaCalibrar = true;
  }
  payload["ok"] = ok;
  payload["girando"] = girandoParaCalibrar;
  payload["duracao_ms"] = orion::DURACAO_CALIBRACAO_BUSSOLA_MS;
  if (!ok) {
    payload["erro"] = bussola.conectado() ? "calibracao_ja_em_andamento" : "bussola_desconectada";
  }
  orion::enviarMensagem(Serial, "RESPONSE", origem, payload.as<JsonObjectConst>(), idMsg);
}

// TEST_WHEEL (bancada, 2026-10-25): gira UMA roda em malha aberta e le os
// dois fios do encoder por amostragem, para provar a fiacao: qual roda o par
// de IN move, se o canal A conta, se o B conta, e em que ordem os dois
// chegam (a ordem e o sentido). Bloqueia no maximo 1,2 s - abaixo dos 2 s do
// watchdog, que ainda assim e alimentado no laco. So para rodas no ar.
// TEST_WHEEL com "lado": "ambos" (2026-10-31): as duas rodas juntas, com a
// regra de espelhamento do movimento normal. "sentido" 1 = frente do robo,
// -1 = re. Responde os pulsos de cada roda - quem olha diz se o robo seria
// empurrado para a frente e se as duas giram juntas.
void responderTesteAmbas(JsonObjectConst p, const char* origem, const char* idMsg) {
  bool frente = (p["sentido"] | 1) >= 0;
  // Teto de duty tambem aqui (C4-02): o `potencia_percent` devolvido e o que
  // a roda RECEBEU, nao o que foi pedido.
  float fracao = (p["potencia_percent"] | 40.0f) / 100.0f;
  fracao = constrain(fracao, 0.0f, orion::DUTY_MAX_FRACAO);
  uint32_t duracaoMs = constrain(p["duracao_ms"] | 800, 50, 1200);
  int64_t esqIni = motores.pulsosBrutos(true), dirIni = motores.pulsosBrutos(false);
  motores.testeAmbasMalhaAberta(frente, fracao);
  unsigned long inicio = millis();
  while (millis() - inicio < duracaoMs) {
    esp_task_wdt_reset();
    delay(5);
  }
  motores.parar();

  JsonDocument payload;
  payload["lado"] = "ambos";
  payload["sentido"] = frente ? 1 : -1;
  payload["potencia_percent"] = fracao * 100.0f;
  payload["duracao_ms"] = duracaoMs;
  payload["pulsos_esq"] = motores.pulsosBrutos(true) - esqIni;
  payload["pulsos_dir"] = motores.pulsosBrutos(false) - dirIni;
  payload["dir_invertido_esq"] = pinos::DIR_INVERTIDO_ESQUERDO;
  payload["dir_invertido_dir"] = pinos::DIR_INVERTIDO_DIREITO;
  orion::enviarMensagem(Serial, "RESPONSE", origem, payload.as<JsonObjectConst>(), idMsg);
}

void responderTesteRoda(JsonObjectConst p, const char* origem, const char* idMsg) {
  if (strcmp(p["lado"] | "esq", "ambos") == 0) {
    responderTesteAmbas(p, origem, idMsg);
    return;
  }
  bool esquerda = strcmp(p["lado"] | "esq", "dir") != 0;
  bool sentidoIn1 = (p["sentido"] | 1) >= 0;
  float fracao = (p["potencia_percent"] | 40.0f) / 100.0f;
  fracao = constrain(fracao, 0.0f, orion::DUTY_MAX_FRACAO);  // ver responderTesteAmbas
  uint32_t duracaoMs = constrain(p["duracao_ms"] | 800, 50, 1200);

  // VARREDURA (2026-10-31): a roda girou e o encoder contou 0, com os fios
  // parados no nivel do pull-up - cara de fio em OUTRO pino. Com
  // "varrer": true, o laco abaixo tambem le TODOS os pinos onde um fio de
  // encoder poderia estar e conta quantas vezes cada um mudou de nivel.
  // So LE: PIN_INPUT_ENABLE liga o buffer de entrada sem tirar a funcao do
  // pino (saida de servo, I2C, rele continuam iguais). Os IN do motor ficam
  // de fora porque o proprio PWM os faz piscar. Rodar uma vez com
  // potencia 0 (base) e outra girando: o pino que so muda girando e o fio.
  static const uint8_t PINOS_VARRER[] = {0, 2, 4, 5, 12, 13, 14, 15, 16, 17,
                                         18, 19, 21, 22, 23, 27, 34, 35};
  constexpr size_t N_VARRER = sizeof(PINOS_VARRER);
  const bool varrer = p["varrer"] | false;
  uint8_t nivelVarrer[N_VARRER] = {0};
  uint8_t nivelIniVarrer[N_VARRER] = {0};
  uint32_t mudancasVarrer[N_VARRER] = {0};
  if (varrer) {
    for (size_t i = 0; i < N_VARRER; i++) {
      PIN_INPUT_ENABLE(GPIO_PIN_MUX_REG[PINOS_VARRER[i]]);
      nivelVarrer[i] = nivelIniVarrer[i] =
          gpio_get_level(static_cast<gpio_num_t>(PINOS_VARRER[i]));
    }
  }

  uint8_t pinoA = esquerda ? pinos::ENCODER_ESQ : pinos::ENCODER_DIR;
  uint8_t pinoB = esquerda ? pinos::ENCODER_ESQ_B : pinos::ENCODER_DIR_B;
  pinMode(pinoB, pinoB < 34 ? INPUT_PULLUP : INPUT);  // A ja e do PCNT

  int aAnt = digitalRead(pinoA), bAnt = digitalRead(pinoB);
  int aIni = aAnt, bIni = bAnt;
  uint32_t bordasA = 0, bordasB = 0, subidaAcomBbaixo = 0, subidaAcomBalto = 0;
  int64_t pcntIni = motores.pulsosBrutos(esquerda);

  motores.testeMalhaAberta(esquerda, sentidoIn1, fracao);
  // Le de volta o NIVEL REAL dos dois IN (o pad, nao o registrador): prova
  // se o ESP32 esta mesmo entregando o sinal, sem precisar de multimetro.
  uint8_t inAtivo = esquerda ? (sentidoIn1 ? pinos::MOTOR_ESQ_IN1 : pinos::MOTOR_ESQ_IN2)
                             : (sentidoIn1 ? pinos::MOTOR_DIR_IN3 : pinos::MOTOR_DIR_IN4);
  uint8_t inOutro = esquerda ? (sentidoIn1 ? pinos::MOTOR_ESQ_IN2 : pinos::MOTOR_ESQ_IN1)
                             : (sentidoIn1 ? pinos::MOTOR_DIR_IN4 : pinos::MOTOR_DIR_IN3);
  PIN_INPUT_ENABLE(GPIO_PIN_MUX_REG[inAtivo]);
  PIN_INPUT_ENABLE(GPIO_PIN_MUX_REG[inOutro]);
  uint32_t amostras = 0, ativoAlto = 0, outroAlto = 0;
  unsigned long inicio = millis();
  while (millis() - inicio < duracaoMs) {
    amostras++;
    ativoAlto += gpio_get_level(static_cast<gpio_num_t>(inAtivo));
    outroAlto += gpio_get_level(static_cast<gpio_num_t>(inOutro));
    int a = digitalRead(pinoA), b = digitalRead(pinoB);
    if (a != aAnt) {
      bordasA++;
      if (a == HIGH) (b == LOW ? subidaAcomBbaixo : subidaAcomBalto)++;
    }
    if (b != bAnt) bordasB++;
    aAnt = a; bAnt = b;
    if (varrer) {
      for (size_t i = 0; i < N_VARRER; i++) {
        uint8_t n = gpio_get_level(static_cast<gpio_num_t>(PINOS_VARRER[i]));
        if (n != nivelVarrer[i]) { mudancasVarrer[i]++; nivelVarrer[i] = n; }
      }
    }
    esp_task_wdt_reset();
  }
  motores.parar();

  JsonDocument payload;
  payload["lado"] = esquerda ? "esq" : "dir";
  payload["sentido"] = sentidoIn1 ? 1 : -1;
  payload["potencia_percent"] = fracao * 100.0f;
  payload["duracao_ms"] = duracaoMs;
  payload["pino_a"] = pinoA;
  payload["pino_b"] = pinoB;
  payload["a_inicio"] = aIni;
  payload["b_inicio"] = bIni;
  payload["bordas_a"] = bordasA;
  payload["bordas_b"] = bordasB;
  payload["subida_a_com_b_baixo"] = subidaAcomBbaixo;
  payload["subida_a_com_b_alto"] = subidaAcomBalto;
  payload["pcnt_a"] = motores.pulsosBrutos(esquerda) - pcntIni;
  payload["in_ativo"] = inAtivo;
  payload["in_ativo_alto_pct"] = amostras ? 100.0f * ativoAlto / amostras : -1.0f;
  payload["in_outro"] = inOutro;
  payload["in_outro_alto_pct"] = amostras ? 100.0f * outroAlto / amostras : -1.0f;
  if (varrer) {
    // "pino": [nivel_inicial, mudancas] - todos, para ver tambem quem ficou parado
    JsonObject v = payload["varredura"].to<JsonObject>();
    char chave[4];
    for (size_t i = 0; i < N_VARRER; i++) {
      snprintf(chave, sizeof(chave), "%u", PINOS_VARRER[i]);
      JsonArray par = v[chave].to<JsonArray>();
      par.add(nivelIniVarrer[i]);
      par.add(mudancasVarrer[i]);
    }
  }
  orion::enviarMensagem(Serial, "RESPONSE", origem, payload.as<JsonObjectConst>(), idMsg);
}

// NACK de comando que este firmware nao conhece (2026-11-10, C4-01). Vai NO
// LUGAR do ACK, nunca depois dele: o send() do Python resolve o pedido no
// primeiro dos dois que chegar (communication/service.py), entao um NACK
// atras de um ACK seria ignorado - e o robo seguiria dizendo "Varrendo a
// frente" para um SCAN_FRONT que nao existe.
void enviarNackDesconhecido(const char* comando, const char* origem, const char* idMsg) {
  JsonDocument payload;
  payload["motivo"] = "comando_desconhecido";
  payload["comando"] = comando;
  orion::enviarMensagem(Serial, "NACK", origem, payload.as<JsonObjectConst>(), idMsg);
}

// Devolve true se o comando foi reconhecido (ACK enviado), false se virou
// NACK.
//
// ORDEM DO ACK (2026-11-10, C4-01). Os quatro comandos tratados aqui
// respondem ACK ANTES de executar: TEST_WHEEL bloqueia ate 1,2 s e o ACK tem
// que chegar dentro dos 500 ms do ack_timeout_ms. Os do CommandExecutor
// respondem DEPOIS de executar() dizer se reconheceu - executar() so ajusta
// alvos e pinos (microssegundos), entao o ACK nao atrasa de forma visivel.
bool tratarComando(JsonDocument& msg, const char* origem, const char* idMsg) {
  const char* comando = msg["payload"]["comando"] | "";

  if (strcmp(comando, "WHO_ARE_YOU") == 0) {
    enviarPayloadVazio("ACK", origem, idMsg);
    responderWhoAreYou(origem, idMsg);
    return true;
  }
  if (strcmp(comando, "RETURN_STATUS") == 0) {
    enviarPayloadVazio("ACK", origem, idMsg);
    responderReturnStatus(origem, idMsg);
    return true;
  }
  if (strcmp(comando, "TEST_WHEEL") == 0) {
    enviarPayloadVazio("ACK", origem, idMsg);
    responderTesteRoda(msg["payload"], origem, idMsg);
    return true;
  }
  if (strcmp(comando, "CALIBRATE_COMPASS") == 0) {
    enviarPayloadVazio("ACK", origem, idMsg);
    responderCalibrarBussola(msg["payload"], origem, idMsg);
    return true;
  }

  JsonObjectConst payload = msg["payload"];
  if (comandos.executar(comando, payload)) {
    enviarPayloadVazio("ACK", origem, idMsg);
    return true;
  }
  enviarNackDesconhecido(comando, origem, idMsg);
  return false;
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
    const char* comando = msg["payload"]["comando"] | "";
    // `comando` aponta para dentro de `msg`, que ninguem altera e que vive
    // ate o fim desta funcao - segue valido depois do tratarComando().
    bool reconhecido = tratarComando(msg, origem, idMsg);
    // Homem-morto: SO comando de movimento renova (C1-05/C4-03,
    // command_executor.h). Antes qualquer COMMAND renovava - o SET_PAN_TILT
    // da visao mantinha vivo para sempre um MOVE_FORWARD sem dono.
    if (reconhecido && orion::renovaHomemMorto(comando, msg["payload"])) {
      ultimoComandoRecebido = millis();
    }
    return;
  }
  if (strcmp(tipo, "HEARTBEAT") == 0) {
    // Nada a responder (o corpo manda o proprio heartbeat a cada 1 s no
    // loop), mas a HORA importa desde 2026-11-10: movimento com fim so
    // continua com o Pi vivo (safety_manager.h).
    ultimoHeartbeatPi = millis();
    heartbeatPiVisto = true;
  }
}

}  // namespace

void setup() {
  // MOTORES PRIMEIRO, ANTES DE TUDO (2026-11-10, auditoria C4-05). Nao ha
  // mais rele cortando a ponte H (pins.h): do reset ate esta linha os IN do
  // L298N ficam em alta impedancia com a ponte energizada, e um IN
  // flutuando pode dar um tranco na roda. Antes esta chamada vinha depois do
  // WiFi, da serial, do I2C, da varredura e da EEPROM - dezenas de ms de
  // janela. Aqui ela fica no minimo que o software consegue; o resto da
  // janela (bootloader + arranque do ESP-IDF) so fecha com pull-down de 10k
  // nos IN1..IN4, que e HARDWARE.
  //
  // Pode vir antes de tudo porque nao depende de nada: pinMode/digitalWrite,
  // ledcSetup (canais 14/15, fora dos que a ESP32Servo pega - motor_manager.h)
  // e o PCNT dos encoders. O ESP32PWM::allocateTimer la embaixo nao mexe
  // nesses canais.
  motores.iniciar();

  // O par MCUSR=0 + wdt_disable() do AVR nao tem equivalente aqui e foi
  // REMOVIDO (2026-08-02). Ele existia porque o bootloader do Mega deixava o
  // watchdog "quente" depois de um reset causado por ele, e o chip podia se
  // reiniciar no meio do proprio boot. O ESP32 nao tem esse defeito: o
  // watchdog de tarefa e desarmado pelo proprio arranque do ESP-IDF e so
  // passa a valer quando esta funcao o arma explicitamente la embaixo.

  // WiFi E BLUETOOTH DESLIGADOS DE PROPOSITO (2026-08-02).
  //
  // O corpo fala com o Raspberry pelo CABO USB. O radio nao transporta nada
  // e desligar ele ganha tres coisas de uma vez:
  //
  //  1. ENERGIA - motivo declarado da troca de placa. O Mega puxava 50-80 mA
  //     so para existir. O ESP32 com radio ligado fica em ~100 mA com picos
  //     de ~400 mA na transmissao, ou seja PIOR que o Mega; com o radio
  //     desligado cai para 20-40 mA, que e o ganho que se queria.
  //  2. O ADC2 VOLTA A EXISTIR. O ESP32 desliga o ADC2 quando o WiFi sobe.
  //     (Dizia que BATERIA_SENSE "foi para o GPIO 36": a placa do dono nao
  //     tem GPIO 36 e o sensor de bateria ainda nao tem pino - pins.h.
  //     Corrigido 2026-11-10.) Quando ele tiver, esta linha e a segunda
  //     trava para leitura analogica.
  //  3. O LOOP NAO ENGASGA. A pilha de WiFi roda em tarefa de prioridade
  //     alta e rouba a CPU em rajadas imprevisiveis. Era critico com os
  //     TB6600 (passo por temporizacao em software virava passo perdido);
  //     com o motor DC o PWM e do LEDC (hardware) e o encoder do PCNT, mas
  //     a malha de 50 Hz, a serial e o homem-morto ainda rodam neste loop.
  //
  // Custo aceito: nao ha OTA. A gravacao e por cabo, que e como ja se faz.
  // Se um dia o radio for reativado, reler os tres itens acima ANTES.
  WiFi.mode(WIFI_OFF);
  btStop();

  // O ESP32 nao aceita -D SERIAL_RX_BUFFER_SIZE (aquilo era um define de
  // compilacao do core AVR); aqui o buffer se ajusta em tempo de execucao, e
  // TEM que ser ANTES do begin() - depois dele a chamada e ignorada em
  // silencio.
  //
  // NAO REMOVER, e nao baixar de 1024: esta linha e a herdeira direta do bug
  // de 2026-07-26. Uma mensagem SET_PAN_TILT tem 251 bytes de JSON (255 com
  // STX/ETX+CRC). Seguindo uma pessoa os comandos chegam em rajada; quando o
  // buffer nao aguenta a segunda mensagem, ela perde bytes, o CRC falha e
  // processarQuadro() da return SILENCIOSO, sem ACK. O sintoma nao parece
  // buffer: a camera engasga e perde o alvo, sem erro nenhum aparecer.
  Serial.setRxBufferSize(1024);

  // 250000 e nao 115200 (2026-07-27). Dois motivos, os dois medidos:
  //
  // 1. TEMPO DE LOOP. O pacote de TELEMETRY passa de 600 bytes e o buffer de
  //    saida do AVR e de 64 - `Serial.write` BLOQUEIA esperando drenar. A
  //    87 us/byte isso dava picos de 84 ms na volta do loop (medido em
  //    loop_max_us, com media de 79 us). Como o eco de um obstaculo a 30 cm
  //    dura 1,7 ms, o robo ficava cego em rajadas: 84 ms perdidos a cada
  //    500 ms de telemetria. A 250000 baud o mesmo pacote leva ~24 ms.
  //
  // 2. ERRO DO GERADOR DE BAUD. No Mega, a 16 MHz, 250000 caia exato
  //    (UBRR=3, erro 0,0%) e 115200 caia em UBRR=8 com 2,1% de erro - a taxa
  //    mais alta era tambem a mais confiavel. No ESP32 esse argumento
  //    ESPECIFICO nao vale mais (a UART tem divisor fracionario e acerta as
  //    duas), mas o motivo 1 continua valendo e a taxa continua sendo 250000
  //    porque o OUTRO LADO ja fala nessa velocidade.
  //
  // O outro lado (communication.hardware_core.baud_rate em config/orion.yaml) TEM
  // que bater - se um lado mudar sozinho, a placa fica muda. Varias
  // ferramentas de bancada em tools/ ficaram para tras em 115200 e falham
  // parecendo hardware morto; conferir isso ANTES de suspeitar da placa.
  Serial.begin(250000);

  // Timeout no barramento I2C da bussola (Cap 18 s.9, achado real da
  // vistoria de 2026-07-24): sem isso, um travamento de I2C (clock
  // stretching infinito por fio solto/ruido) prendia Wire.requestFrom()
  // para sempre dentro de um atualizar() de sensor - e como isso roda
  // ANTES de safety.avaliar() no mesmo loop(), a seguranca reativa inteira
  // parava junto. 25ms e generoso mas pequeno o suficiente pra nao atrasar
  // o loop de forma perceptivel; "true" reinicia o periferico I2C
  // automaticamente apos o timeout, sem exigir reset do chip inteiro.
  // OS PINOS SAO OBRIGATORIOS AQUI (2026-08-02). No Mega o I2C morava em
  // pinos fixos de silicio (20/21) e Wire.begin() sem argumento acertava
  // sozinho. No ESP32 qualquer par de GPIO serve, entao o begin() sem
  // argumento assume o padrao da placa - e se ele nao for o par que esta
  // soldado, o barramento fica MUDO sem erro nenhum: nenhuma excecao,
  // nenhum retorno ruim, so `bussola_conectada: false` na telemetria,
  // exatamente o sintoma que ja custou uma investigacao em 2026-07-27.
  Wire.begin(pinos::BUSSOLA_SDA, pinos::BUSSOLA_SCL);
  // 400 kHz em vez dos 100 kHz padrao (2026-07-27). MEDIDO: com 100 kHz o
  // I2C consumia 99,4 ms a cada 500 ms de telemetria (t_i2c_us) - 20% de
  // todo o tempo do robo. A bussola (HMC5883L/QMC5883L) suporta 400 kHz
  // (fast mode) por datasheet.
  // Enquanto o loop esta preso no I2C ele nao olha o pino ECHO do ultrassom,
  // e o eco de um obstaculo a 30 cm dura 1,7 ms - perder essas janelas faz o
  // sensor concluir "nada refletiu" e reportar caminho LIVRE (fail-open, o
  // lado errado para falhar).
  Wire.setClock(400000);
  // Era Wire.setWireTimeout(25000, true) no Mega - microssegundos, com
  // reinicio automatico do periferico. A Wire do ESP32 nao tem esse metodo;
  // o equivalente e setTimeOut, que fala em MILISSEGUNDOS. 25000 us = 25 ms,
  // entao o numero muda mas o prazo e o mesmo.
  //
  // O que este timeout impede (Cap 18 s.9, achado real da vistoria de
  // 2026-07-24): sem ele, um travamento de I2C - fio solto ou ruido segurando
  // o clock - prendia Wire.requestFrom() PARA SEMPRE dentro de um
  // bussola.atualizar(). E como isso roda ANTES de safety.avaliar() no mesmo
  // loop(), a seguranca reativa inteira parava junto.
  Wire.setTimeOut(25);

  // Antes de qualquer manager tocar no barramento: retrato limpo de quem
  // esta la de verdade (ver escanearI2c).
  escanearI2c();

  // EEPROM.begin() TEM que vir antes de qualquer manager iniciar, porque
  // bussola.iniciar() ja faz EEPROM.get() da calibracao guardada. No Mega
  // isso nao existia: a EEPROM era memoria de verdade, sempre disponivel. No
  // ESP32 ela e emulada em flash e precisa ser "montada" primeiro - sem esta
  // linha, o get() le lixo e a calibracao aparece como ausente (o teste de
  // numero magico salva de usar o lixo, mas o robo perde a calibracao a cada
  // boot).
  //
  // 128 bytes sobra folgado para o UNICO bloco restante: a bussola no
  // endereco 64 (26 bytes). Aumentar isto e barato; diminuir corrompe
  // silenciosamente o bloco de cima.
  EEPROM.begin(128);

  // Reserva os quatro timers de PWM para os servos ANTES de qualquer
  // attach() (exigencia da ESP32Servo, 2026-08-02). Sao 2 servos desde
  // 19/09 - pan e tilt; o do radar saiu com o ultrassom. A reserva continua
  // sendo dos quatro timers de proposito: eles nao custam nada parados, e
  // reduzir para dois so criaria um limite novo no dia em que aparecer mais
  // um servo. Sem esta reserva o primeiro attach() pega um timer que o
  // LEDC pode estar usando para outra coisa; o servo entao treme ou fica
  // parado sem erro nenhum. Nao existia no Mega porque la a <Servo.h> era
  // dona absoluta do Timer1.
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);

  // motores.iniciar() ja rodou - e a primeira linha do setup() (C4-05).
  bussola.iniciar();
  dht.iniciar();
  // Bateria SEM PINO: o GPIO 36 que o mapa antigo reservava nao existe na
  // placa do dono, e o divisor de tensao nunca foi montado (pins.h).
  // Desligado ate escolher um pino ADC1 livre e recalcular o divisor para
  // 3,3V. (Corrigido 2026-11-10: dizia que o 36 "virou encoder".)
  // bateria.iniciar(<pino ADC1>);

  // O AUTOTESTE DE PRESENCA DO ULTRASSOM saiu junto com o sensor (19/09).
  // Ele existia porque o latch de presenca so fechava com um eco de verdade,
  // e sem ele alguem tinha que passar a mao na frente do sensor a cada
  // reinicio para destravar o robo. Um problema inteiro que deixou de
  // existir com o sensor.
  comandos.iniciar();
  g_payloadVazio.to<JsonObject>();  // forca virar {} em vez de null ao serializar

  estados.transicionarPara(orion::Estado::READY);
  estados.transicionarPara(orion::Estado::IDLE);
  ultimoComandoRecebido = millis();

  // Watchdog de hardware (Cap 18 s.9, achado real da vistoria de
  // 2026-07-24): rede de seguranca final contra QUALQUER travamento do
  // loop() (I2C, biblioteca de terceiros, bug futuro) que o timeout do I2C
  // acima nao cubra - se o loop() nao chamar wdt_reset() por 2s seguidos,
  // o proprio chip se reinicia sozinho.
  //
  // CORRIGIDO 2026-11-10 (C4-05): aqui dizia que reiniciar era seguro porque
  // "o rele dos motores fica desenergizado no boot". O rele SAIU do robo em
  // 2026-10-25 - o L298N esta direto na fonte. O que acontece de verdade num
  // reset por watchdog: os IN flutuam ate o motores.iniciar() (primeira
  // linha deste setup) e a roda pode dar um tranco; depois disso ficam em
  // LOW e o robo para. Fechar a janela inteira pede pull-down nos IN (pins.h).
  //
  // A API mudou de familia junto com a placa. O guarda de versao existe
  // porque o Arduino-ESP32 2.x (ESP-IDF 4.4) e o 3.x (ESP-IDF 5.x) assinam
  // esp_task_wdt_init() de formas incompativeis, e qual deles o PlatformIO
  // baixa depende da versao da plataforma - nao da para escolher um so e
  // torcer.
#if ESP_IDF_VERSION_MAJOR >= 5
  esp_task_wdt_config_t configWdt = {
      .timeout_ms = 2000,
      .idle_core_mask = 0,  // nao vigia as tarefas ociosas, so a nossa
      .trigger_panic = true,
  };
  esp_task_wdt_init(&configWdt);
#else
  esp_task_wdt_init(2, true);  // segundos, no 4.4
#endif
  // Inscreve a tarefa que roda o loop() (NULL = "a tarefa atual"). Sem esta
  // linha o watchdog fica armado vigiando ninguem - e um watchdog que nao
  // vigia e pior que nenhum, porque da falsa sensacao de rede.
  esp_task_wdt_add(NULL);
}

void loop() {
  esp_task_wdt_reset();

  {
    unsigned long agoraUs = micros();
    if (loopAnteriorUs != 0) {
      unsigned long duracao = agoraUs - loopAnteriorUs;  // seguro no overflow
      if (duracao > loopMaxUs) loopMaxUs = duracao;
      loopSomaUs += duracao;
      loopVoltas++;
    }
    loopAnteriorUs = agoraUs;
  }

  while (Serial.available() > 0) {
    uint8_t byte = Serial.read();
    if (decodificador.alimentar(byte)) {
      processarQuadro(decodificador.buffer(), decodificador.tamanho());
    }
  }

  // A bussola de agora vai para a malha que mantem a reta (motor_manager.h).
  motores.informarRumo(bussola.rumoValido(), bussola.rumoGraus());
  motores.atualizar();
  dht.atualizarSeParado(!motores.emMovimento());
  // bateria.atualizar();  // ver nota em setup() - pino virou encoder

  unsigned long agora = millis();

  {
    // A bussola tem ritmo proprio (100 ms) e ela mesma controla isso. A
    // maioria das chamadas retorna na hora (ainda nao deu o intervalo) - por
    // isso `leituras_bussola` conta so as que custaram algo, senao a media
    // ficaria diluida por milhares de retornos imediatos.
    unsigned long inicioUs = micros();
    bussola.atualizar();
    unsigned long custo = micros() - inicioUs;
    if (custo > 100) {  // 100us: separa leitura de verdade de retorno imediato
      tempoBussolaAcumuladoUs += custo;
      leiturasBussola++;
    }
  }

  // Giro da calibracao: acaba sozinho quando a coleta acaba (25 s). Se
  // alguem mandou STOP no meio, as rodas ja pararam e so falta esquecer.
  if (girandoParaCalibrar) {
    // SEM O PI, NAO GIRA (auditoria 12/11). O giro renova o homem-morto
    // sozinho ate a coleta acabar (25 s) - e quem para o robo se aparecer
    // obstaculo no raio de giro e a trava do LiDAR, que roda NO PI. Com o Pi
    // travado, o robo girava as cegas ate o fim. Mesmo criterio do movimento
    // com fim (safety_manager.h): heartbeat do Pi velho = para.
    const bool piVivo = heartbeatPiVisto &&
                        (millis() - ultimoHeartbeatPi) <= orion::TIMEOUT_HEARTBEAT_PI_MS;
    if (!bussola.calibrando() || !motores.emMovimento() || !piVivo) {
      motores.parar();
      girandoParaCalibrar = false;
    } else {
      // O homem-morto (5 s sem comando) pararia o giro no meio da coleta.
      // Aqui ha um fim garantido - a propria calibracao - entao o giro conta
      // como comando vivo ate ela acabar.
      ultimoComandoRecebido = millis();
    }
  }

  // Movimento com fim (distancia/angulo) nao e cortado pelo homem-morto de
  // comando: 50 cm levam ~10 s, mais que os 5 s (2026-10-31). Ate
  // 2026-11-10 este trecho renovava o homem-morto a cada volta enquanto ele
  // durasse - com o Pi travado o robo andava ate o fim (2 m ~ 45 s). Agora
  // quem o mantem vivo e o HEARTBEAT do Pi, dentro do safety.avaliar().
  const unsigned long idadeHeartbeatPi =
      heartbeatPiVisto ? millis() - ultimoHeartbeatPi : orion::HEARTBEAT_PI_NUNCA;
  bool acionouParadaAgora = safety.avaliar(motores, ultimoComandoRecebido, idadeHeartbeatPi);
  if (acionouParadaAgora) {
    // "frente_desconhecida" tambem bloqueia o movimento, mas nao e a mesma
    // coisa que ver um obstaculo - e o radar estar olhando pro lado sem
    // medida recente da frente. Os dois viram OBSTACLE_DETECTED porque e o
    // estado que freia; QUAL dos dois foi vai na telemetria, em
    // seguranca_motivo, para o diagnostico parar de mentir (2026-07-28).
    bool ehObstaculo = strcmp(safety.motivo(), "obstaculo_frontal") == 0 ||
                       strcmp(safety.motivo(), "frente_desconhecida") == 0;
    estados.transicionarPara(ehObstaculo ? orion::Estado::OBSTACLE_DETECTED
                                          : orion::Estado::SAFE_MODE);
  } else if (!safety.pararAtivo() && !motores.emMovimento() &&
             (estados.atual() == orion::Estado::OBSTACLE_DETECTED ||
              estados.atual() == orion::Estado::EXECUTING_MISSION ||
              estados.atual() == orion::Estado::SAFE_MODE)) {
    // SAFE_MODE entrou nesta lista em 2026-07-26. Antes disso ele era um
    // estado SEM SAIDA: o robo entrava e nunca mais voltava a IDLE sozinho,
    // mesmo com todos os gatilhos limpos (medido na bancada: frontal 245 de
    // 25 cm, radar em 90, motores parados - e ainda assim preso). Bastava UM
    // ciclo de leitura ruim do
    // ultrassom, dos que a vibracao do motor de passo provoca, para travar o
    // robo ate alguem reiniciar na mao - inviavel para as 72h de operacao
    // continua que o projeto persegue.
    //
    // A seguranca NAO afrouxa: a saida continua exigindo perigo encerrado
    // (!safety.pararAtivo()) e motores parados (!motores.emMovimento()). O
    // robo segue parando na hora do perigo; a mudanca e que agora ele se
    // RECUPERA quando o perigo passa.
    estados.transicionarPara(orion::Estado::IDLE);
  }

  notificarTransicaoDeEstado();

  if (agora - ultimoHeartbeat >= INTERVALO_HEARTBEAT_MS) {
    ultimoHeartbeat = agora;
    enviarPayloadVazio("HEARTBEAT", "motion_core");
  }

  bool movendo = motores.emMovimento();
  unsigned long intervaloTelemetria =
      movendo ? INTERVALO_TELEMETRIA_MOVENDO_MS : INTERVALO_TELEMETRIA_PARADO_MS;

  if (agora - ultimaTelemetria >= intervaloTelemetria) {
    ultimaTelemetria = agora;
    JsonDocument payload;
    if (movendo) {
      // Andando: so o essencial, e sem os campos de diagnostico abaixo -
      // cada byte aqui e tempo de olho fechado para o ultrassom.
      telemetria.preencherPayloadReduzido(payload.to<JsonObject>());
      unsigned long inicioEnvioUs = micros();
      orion::enviarMensagem(Serial, "TELEMETRY", "motion_core", payload.as<JsonObjectConst>());
      tempoEnvioAnteriorUs = micros() - inicioEnvioUs;
      return;  // nada de instrumentacao no caminho quente
    }
    telemetria.preencherPayload(payload.to<JsonObject>());
    // Tempo de volta do loop desde a ultima telemetria (ver comentario nas
    // globais). loop_max_us acima de ~1700 significa que ecos de obstaculo
    // proximo estao sendo perdidos.
    // Resultado da varredura de I2C do boot (ver escanearI2c). Vai na
    // telemetria por ser a unica janela que temos para o barramento - a
    // serial e exclusiva do protocolo, entao nao ha como imprimir.
    payload["i2c_total"] = g_i2cTotal;
    JsonArray encontrados = payload["i2c_enderecos"].to<JsonArray>();
    for (uint8_t i = 0; i < g_i2cTotal && i < I2C_MAX_ENCONTRADOS; i++) {
      encontrados.add(g_i2cEncontrados[i]);
    }

    payload["loop_max_us"] = loopMaxUs;
    payload["loop_media_us"] = loopVoltas > 0 ? (loopSomaUs / loopVoltas) : 0;
    // Quanto o envio ANTERIOR desta mesma telemetria custou, e quanto o I2C
    // (bussola) gastou desde entao. Somados ao resto do loop, dizem onde os
    // ~50ms sem explicacao estao - ver o bloco das globais.
    payload["t_envio_us"] = tempoEnvioAnteriorUs;
    payload["t_bussola_us"] = tempoBussolaAcumuladoUs;
    payload["n_bussola"] = leiturasBussola;
    // Quadros recebidos e jogados fora (CRC ruim ou maiores que CAPACIDADE),
    // acumulado desde o boot (2026-11-10, C4-06). Um comando que "some" sem
    // ACK aparece aqui como contador subindo, em vez de silencio.
    payload["quadros_invalidos"] = decodificador.quadrosInvalidos();
    // O commit gravado (C4-07, protocolo.h) - parado, 1 vez por segundo.
    payload["firmware_git"] = orion::COMMIT_FIRMWARE;
    loopMaxUs = 0;
    loopSomaUs = 0;
    loopVoltas = 0;
    tempoBussolaAcumuladoUs = 0;
    leiturasBussola = 0;

    unsigned long inicioEnvioUs = micros();
    orion::enviarMensagem(Serial, "TELEMETRY", "motion_core", payload.as<JsonObjectConst>());
    tempoEnvioAnteriorUs = micros() - inicioEnvioUs;
  }
}
