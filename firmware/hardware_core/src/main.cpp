// Hardware Core - firmware completo (Cap 10).
//
// Serial (pinos 0/1, USB) e EXCLUSIVA do protocolo com o Raspberry - nunca
// usar Serial.print() aqui para depuracao, corromperia o enquadramento
// binario.
//
// Fiacao confirmada nesta montagem (guia de ligacao eletrica, atualizado
// 2026-07-20): motores (pinos 2-6), ultrassom frontal (26/27) e traseiro
// (22/23) - CORRIGIDO 2026-07-20: estavam trocados, tapar o traseiro mexia
// na distancia frontal; ver nota em pins.h. IMU I2C (20/21), DHT (24),
// servo do radar (9) e servos pan/tilt (10/11). Encoders e LED RESERVADOS -
// ver pins.h - ainda nao ligados fisicamente.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <avr/wdt.h>
#include <string.h>

#include "bateria_manager.h"
#include "bussola_manager.h"
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
orion::BussolaManager bussola;
orion::CommandExecutor comandos(motores, radar, estados, safety);
orion::TelemetryManager telemetria(motores, radar, imu, dht, estados, ultrassomTraseiro,
                                  bateria, bussola, encoders);

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
constexpr unsigned long INTERVALO_IMU_MS = 50;

unsigned long ultimoHeartbeat = 0;
unsigned long ultimaTelemetria = 0;
unsigned long ultimoImu = 0;
unsigned long ultimoComandoRecebido = 0;

// Instrumentacao do tempo de volta do loop (2026-07-25). Motivo real: o
// ultrassom e lido por sondagem, e o eco de um obstaculo a 30 cm dura so
// 1,7 ms - se a volta do loop demorar mais que isso, o pulso comeca e
// termina entre duas leituras do pino e a distancia nunca fica valida.
// Medido ao vivo: zero leituras validas em 18 s com obstaculo a ~1 m.
// Estes numeros vao na telemetria para achar o culpado com dado, em vez
// de palpite (suspeitos: I2C do IMU com timeout de 25 ms, e o Serial.write
// da telemetria, que bloqueia quando o buffer de 64 bytes enche).
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
// pacote informar o proprio tempo de envio); `t_i2c_us` acumula o que IMU e
// bussola gastaram desde o ultimo pacote.
unsigned long tempoEnvioAnteriorUs = 0;
// Separados desde a terceira rodada: `t_i2c_us` junto acusava 99,4 ms por
// janela de 500 ms, mas subir o barramento de 100 kHz para 400 kHz nao
// mudou NADA (99,3 ms) - logo nao e tempo de transferencia, e espera. Com
// IMU e bussola no mesmo numero nao dava para saber de quem era: 99,4/10
// leituras da IMU = 9,94 ms, mas 99,4/5 leituras da bussola = 19,9 ms, e
// os dois sao plausiveis. Contadores separados resolvem em uma gravacao.
unsigned long tempoImuAcumuladoUs = 0;
unsigned long tempoBussolaAcumuladoUs = 0;
uint16_t leiturasImu = 0;
uint16_t leiturasBussola = 0;
orion::Estado estadoAnterior = orion::Estado::BOOT;
bool varrendoAnterior = false;

JsonDocument g_payloadVazio;  // reutilizado para ACK/HEARTBEAT (sempre {})

// DIAGNOSTICO DE I2C (2026-07-27). Motivo: a MPU6050 e a bussola QMC6310
// dividem os pinos 20/21 e as duas apareceram mudas na telemetria
// (`imu_conectado` e `bussola_conectada` ambos false). Sem enxergar o
// barramento, "o modulo esta quebrado", "o endereco e outro" e "o barramento
// nao sobe" sao indistinguiveis de fora - e cada um pede um conserto
// diferente. Uma varredura unica no boot resolve a duvida com dado.
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

// Comeca a calibracao da bussola e responde NA HORA, sem esperar os 25 s de
// coleta. Tem que ser assim: o watchdog de 2 s reiniciaria o Mega se este
// handler ficasse esperando, e a serial ficaria muda enquanto isso. Quem
// pediu acompanha o fim pelos campos `bussola_calibrando`/`bussola_calibrada`
// da telemetria, que sai a cada 500 ms.
//
// Durante a coleta o robo precisa GIRAR para todos os lados - quem comanda
// isso e o Raspberry (regra 3: o Arduino nunca decide movimento sozinho).
void responderCalibrarBussola(const char* origem, const char* idMsg) {
  JsonDocument payload;
  bool ok = bussola.iniciarCalibracao();
  payload["ok"] = ok;
  payload["duracao_ms"] = orion::DURACAO_CALIBRACAO_BUSSOLA_MS;
  if (!ok) {
    payload["erro"] = bussola.conectado() ? "calibracao_ja_em_andamento" : "bussola_desconectada";
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
  if (strcmp(comando, "CALIBRATE_COMPASS") == 0) {
    responderCalibrarBussola(origem, idMsg);
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
  // Limpa a flag de reset por watchdog e desliga o watchdog ANTES de tudo -
  // padrao de seguranca AVR: alguns bootloaders (incluindo o usado neste
  // Mega) nao desligam o watchdog sozinhos apos um reset causado por ele,
  // e um watchdog ainda "quente" com timeout curto pode reiniciar o chip
  // no meio do proprio boot antes deste setup() terminar. So depois disso
  // e seguro rearma-lo no fim da funcao.
  MCUSR = 0;
  wdt_disable();

  // 250000 e nao 115200 (2026-07-27). Dois motivos, os dois medidos:
  //
  // 1. TEMPO DE LOOP. O pacote de TELEMETRY passa de 600 bytes e o buffer de
  //    saida do AVR e de 64 - `Serial.write` BLOQUEIA esperando drenar. A
  //    87 us/byte isso dava picos de 84 ms na volta do loop (medido em
  //    loop_max_us, com media de 79 us). Como o eco de um obstaculo a 30 cm
  //    dura 1,7 ms, o robo ficava cego em rajadas: 84 ms perdidos a cada
  //    500 ms de telemetria. A 250000 baud o mesmo pacote leva ~24 ms.
  //
  // 2. ERRO DO GERADOR DE BAUD. A 16 MHz, 250000 cai exato (UBRR=3, erro
  //    0,0%); 115200 cai em UBRR=8 com 2,1% de erro. Ou seja, a taxa mais
  //    alta e tambem a mais confiavel neste chip.
  //
  // O outro lado (communication.arduino.baud_rate em config/orion.yaml)
  // TEM que bater - se um lado mudar sozinho, o Mega fica mudo.
  Serial.begin(250000);

  // Timeout no barramento I2C do IMU (Cap 18 s.9, achado real da vistoria
  // de 2026-07-24): sem isso, um travamento de I2C (clock stretching
  // infinito por fio solto/ruido) prendia Wire.requestFrom() para sempre
  // dentro de imu.atualizar() - e como isso roda ANTES de safety.avaliar()
  // no mesmo loop(), a seguranca reativa inteira parava junto. 25ms e
  // generoso (uma leitura normal do MPU6050 leva bem menos que isso) mas
  // pequeno o suficiente pra nao atrasar o loop de forma perceptivel;
  // "true" reinicia o periferico I2C automaticamente apos o timeout, sem
  // exigir reset do chip inteiro.
  Wire.begin();
  // 400 kHz em vez dos 100 kHz padrao (2026-07-27). MEDIDO: com 100 kHz o
  // I2C consumia 99,4 ms a cada 500 ms de telemetria (t_i2c_us) - 20% de
  // todo o tempo do robo, com uma leitura da IMU levando ~10 ms. Tanto a
  // MPU6050 quanto o QMC6310 suportam 400 kHz (fast mode) por datasheet.
  // Enquanto o loop esta preso no I2C ele nao olha o pino ECHO do ultrassom,
  // e o eco de um obstaculo a 30 cm dura 1,7 ms - perder essas janelas faz o
  // sensor concluir "nada refletiu" e reportar caminho LIVRE (fail-open, o
  // lado errado para falhar).
  Wire.setClock(400000);
  Wire.setWireTimeout(25000, true);

  // Antes de qualquer manager tocar no barramento: retrato limpo de quem
  // esta la de verdade (ver escanearI2c).
  escanearI2c();

  motores.iniciar();
  encoders.iniciar();
  radar.iniciar();
  imu.iniciar();
  // Depois da IMU de proposito: as duas dividem o barramento I2C (20/21) e
  // a bussola usa o acelerometro dela para compensar inclinacao. Ausente e
  // tolerado (Cap 6 s.8) - o retorno so vira `bussola_conectada: false` na
  // telemetria.
  bussola.iniciar();
  dht.iniciar();
  bateria.iniciar(pinos::BATERIA_SENSE);
  ultrassomTraseiro.iniciar(pinos::ULTRASSOM_TRAS_TRIG, pinos::ULTRASSOM_TRAS_ECHO);

  // AUTOTESTE DE PRESENCA DO ULTRASSOM (2026-07-25).
  //
  // O latch de presenca (sensor_ultrassonico.h) so fecha quando um eco de
  // verdade chega, e ate fechar a seguranca trata o sensor como ausente e
  // impede o robo de andar. Com o radar parado apontando para um vao livre,
  // esse eco nunca vinha - e alguem precisava passar a mao na frente do
  // sensor apos CADA reinicio para destravar o robo. Inviavel para um robo
  // que deve patrulhar sozinho.
  //
  // A varredura resolve sozinha: o arco de 30..150 graus cobre o ambiente,
  // e basta UM angulo encontrar superficie para o latch fechar. Numa sala
  // qualquer isso acontece em segundos. Se nao houver NADA em nenhum
  // angulo, o latch fica aberto - e ai a recusa em andar esta correta,
  // porque nao ha como distinguir isso de um sensor desligado.
  radar.iniciarVarredura();
  comandos.iniciar();
  g_payloadVazio.to<JsonObject>();  // forca virar {} em vez de null ao serializar

  estados.transicionarPara(orion::Estado::READY);
  estados.transicionarPara(orion::Estado::IDLE);
  ultimoComandoRecebido = millis();

  // Watchdog de hardware (Cap 18 s.9, achado real da vistoria de
  // 2026-07-24): rede de seguranca final contra QUALQUER travamento do
  // loop() (I2C, biblioteca de terceiros, bug futuro) que o timeout do I2C
  // acima nao cubra - se o loop() nao chamar wdt_reset() por 2s seguidos,
  // o proprio chip se reinicia sozinho. E seguro reiniciar assim porque o
  // rele dos motores (motor_manager.h) ja fica desenergizado por padrao no
  // boot (fail-safe confirmado na vistoria).
  wdt_enable(WDTO_2S);
}

void loop() {
  wdt_reset();

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

  motores.atualizar();
  // Logo DEPOIS de motores.atualizar(), nunca antes: o sentido tem que ser
  // o que os motores estao executando agora. Um pulso que chegasse com o
  // sentido do ciclo anterior seria contado para o lado errado - e no
  // instante da inversao, que e justo quando a odometria mais erra.
  encoders.definirSentido(motores.sentidoFrenteEsquerda(), motores.sentidoFrenteDireita());
  radar.atualizar();
  ultrassomTraseiro.atualizar();
  dht.atualizarSeParado(!motores.emMovimento());
  bateria.atualizar();

  unsigned long agora = millis();

  if (agora - ultimoImu >= INTERVALO_IMU_MS) {
    ultimoImu = agora;
    unsigned long inicioUs = micros();
    imu.atualizar();
    tempoImuAcumuladoUs += micros() - inicioUs;
    leiturasImu++;
  }

  {
    // Fora do bloco do IMU: a bussola tem ritmo proprio (100 ms) e ela mesma
    // controla isso. Reaproveita a ultima leitura do acelerometro em vez de
    // ler a MPU6050 de novo pelo I2C. A maioria das chamadas retorna na hora
    // (ainda nao deu o intervalo) - por isso `leituras_bussola` conta so as
    // que custaram algo, senao a media ficaria diluida por milhares de
    // retornos imediatos.
    unsigned long inicioUs = micros();
    bussola.atualizar(imu.acelX(), imu.acelY(), imu.acelZ());
    unsigned long custo = micros() - inicioUs;
    if (custo > 100) {  // 100us: separa leitura de verdade de retorno imediato
      tempoBussolaAcumuladoUs += custo;
      leiturasBussola++;
    }
  }

  bool acionouParadaAgora = safety.avaliar(motores, radar, imu, ultimoComandoRecebido);
  if (acionouParadaAgora) {
    bool ehObstaculo = strcmp(safety.motivo(), "obstaculo_frontal") == 0;
    estados.transicionarPara(ehObstaculo ? orion::Estado::OBSTACLE_DETECTED
                                          : orion::Estado::SAFE_MODE);
  } else if (!safety.pararAtivo() && !motores.emMovimento() &&
             (estados.atual() == orion::Estado::OBSTACLE_DETECTED ||
              estados.atual() == orion::Estado::EXECUTING_MISSION ||
              estados.atual() == orion::Estado::SAFE_MODE)) {
    // SAFE_MODE entrou nesta lista em 2026-07-26. Antes disso ele era um
    // estado SEM SAIDA: o robo entrava e nunca mais voltava a IDLE sozinho,
    // mesmo com todos os gatilhos limpos (medido na bancada: inclinacao 8 de
    // 20 graus, frontal 245 de 25 cm, radar em 90, sem impacto, motores
    // parados - e ainda assim preso). Bastava UM ciclo de leitura ruim do
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
  notificarFimDeVarredura();

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
    // (IMU + bussola) gastou desde entao. Somados ao resto do loop, dizem
    // onde os ~50ms sem explicacao estao - ver o bloco das globais.
    payload["t_envio_us"] = tempoEnvioAnteriorUs;
    payload["t_imu_us"] = tempoImuAcumuladoUs;
    payload["n_imu"] = leiturasImu;
    payload["t_bussola_us"] = tempoBussolaAcumuladoUs;
    payload["n_bussola"] = leiturasBussola;
    loopMaxUs = 0;
    loopSomaUs = 0;
    loopVoltas = 0;
    tempoImuAcumuladoUs = 0;
    tempoBussolaAcumuladoUs = 0;
    leiturasImu = 0;
    leiturasBussola = 0;

    unsigned long inicioEnvioUs = micros();
    orion::enviarMensagem(Serial, "TELEMETRY", "motion_core", payload.as<JsonObjectConst>());
    tempoEnvioAnteriorUs = micros() - inicioEnvioUs;
  }
}
