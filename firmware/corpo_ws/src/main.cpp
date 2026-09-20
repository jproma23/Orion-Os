// Corpo do Fofao - ESP32 falando WebSocket com o Raspberry.
//
// POR QUE ESTE FIRMWARE EXISTE (2026-09-20). O ESP32 do corpo rodava um
// firmware WebSocket SEM FONTE: procurado no Pi, no Windows e no
// repositorio, nao existia em lugar nenhum. A unica copia era a flash do
// chip, e o protocolo teve de ser recuperado com `strings` no binario.
//
// Este arquivo e aquele firmware reescrito, com quatro buracos tapados.
//
// ------------------------------------------------------------------------
// O QUE O FIRMWARE ANTIGO NAO FAZIA - e o que muda aqui
//
// 1. NAO ANDAVA DISTANCIA. So aceitava `drive` continuo: andava ate alguem
//    mandar `stop`. Para "ande 50 cm" quem contava os pulsos e freava era o
//    PI, com o WiFi no meio da malha - o EDR-0031 desaconselha isso com
//    razao ("laco de velocidade sobre WiFi produz jitter"). Aqui `move` e
//    `turn` terminam DENTRO do ESP32, com o PID e o encoder locais; a rede
//    so entrega a ordem e recebe o aviso de que acabou.
//
// 2. NAO TINHA FREIO PROPRIO. Com o robo andando, perder o WiFi significava
//    continuar andando. O WATCHDOG abaixo para os motores sozinho depois de
//    TIMEOUT_COMANDO_MS sem ordem nenhuma - e ele nao depende da rede para
//    agir, que e justamente o ponto.
//
// 3. NAO MANDAVA ACK. O ComunicacaoService espera confirmacao e retransmite
//    3x sem ela; a ponte no Pi precisou FORJAR o ACK. Aqui cada comando
//    responde `{"ack":<id>,"ok":true|false}` de verdade.
//
// 4. NAO TINHA BUSSOLA NEM DHT. `rumo_graus` e `temperatura_c` sumiram da
//    telemetria quando o firmware trocou. Voltam aqui.
//
// ------------------------------------------------------------------------
// COMPATIBILIDADE: os comandos do firmware antigo continuam valendo
// (`drive`, `stop`, `gimbal`, `ping`) e a telemetria mantem os campos
// antigos com os MESMOS nomes (`passos_esq`, `pan_us`, ...). A ponte do Pi
// (motion_core/navigation/ponte_corpo_ws.py) funciona sem alteracao; o que
// ela ganha e poder parar de fingir - ver o cabecalho dela.
//
// A UNIDADE DO GIMBAL, que no firmware antigo ninguem sabia qual era, aqui e
// GRAU, declarada: `{"cmd":"gimbal","pan":-30}` gira 30 graus para a
// esquerda. A telemetria continua publicando `pan_us` porque e o que o robo
// realmente manda ao servo.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <WebSocketsServer.h>
#include <WiFi.h>

#include "bussola_manager.h"
#include "dht_manager.h"
#include "motor_manager.h"
#include "pins.h"
#include "safety_manager.h"
#include "segredos_wifi.h"

namespace {

// ---------------------------------------------------------------- watchdog
//: Sem NENHUM comando por este tempo, os motores param sozinhos.
//:
//: 1,5 s e bem mais curto que os 5 s do firmware serial, e de proposito: um
//: cabo que cai e um evento raro, um WiFi que engasga nao e. Com 5 s o robo
//: andaria as cegas por cinco segundos a cada queda de pacote.
//:
//: Isto NAO substitui o `stop` do Pi - e a rede de baixo, para quando o Pi
//: nao tiver como mandar nada.
constexpr unsigned long TIMEOUT_COMANDO_MS = 1500;

//: De quanto em quanto tempo a telemetria sai. ~2 Hz, o mesmo ritmo do
//: firmware antigo - o Pi ja esta calibrado para esse intervalo (o
//: TIMEOUT_TELEMETRIA_S da ponte sao doze silencios seguidos).
constexpr unsigned long INTERVALO_TELEMETRIA_MS = 500;

// ------------------------------------------------------------------ gimbal
//: Curso util dos servos, em graus. Fora disto e batente - e servo em
//: batente puxa mais de 1 A desta placa, o que ja derrubou este ESP32 antes
//: (a USB entrega 100 mA). Limitar aqui e mais seguro que confiar em quem
//: manda: o Pi tambem limita, mas o firmware e a ultima linha.
constexpr float GIMBAL_GRAU_MIN = -90.0f;
constexpr float GIMBAL_GRAU_MAX = 90.0f;
//: Pulso do servo no centro e quanto vale um grau. Padrao de servo hobby.
constexpr int GIMBAL_US_CENTRO = 1500;
constexpr float GIMBAL_US_POR_GRAU = 500.0f / 90.0f;

int grausParaUs(float graus) {
  const float limitado = constrain(graus, GIMBAL_GRAU_MIN, GIMBAL_GRAU_MAX);
  return GIMBAL_US_CENTRO + static_cast<int>(limitado * GIMBAL_US_POR_GRAU);
}

// ------------------------------------------------------------------ estado
WebSocketsServer servidor(WS_PORTA);
orion::MotorManager motores;
orion::BussolaManager bussola;
orion::DhtManager dht;
orion::SafetyManager seguranca;

Servo servoPan;
Servo servoTilt;
int panUs = GIMBAL_US_CENTRO;
int tiltUs = GIMBAL_US_CENTRO;

unsigned long ultimoComandoMs = 0;
unsigned long ultimaTelemetriaMs = 0;
//: O `move`/`turn` termina sozinho; quem pediu precisa saber. Guardamos a
//: borda de "estava andando -> parou" para avisar UMA vez.
bool estavaEmMovimento = false;

// ------------------------------------------------------------- utilitarios
void responder(uint8_t cliente, const JsonDocument& doc) {
  String saida;
  serializeJson(doc, saida);
  servidor.sendTXT(cliente, saida);
}

void responderAck(uint8_t cliente, const char* id, bool ok, const char* erro = nullptr) {
  JsonDocument doc;
  doc["ack"] = id ? id : "";
  doc["ok"] = ok;
  if (erro != nullptr) doc["erro"] = erro;
  responder(cliente, doc);
}

// ------------------------------------------------------------- telemetria
void publicarTelemetria() {
  JsonDocument doc;

  // --- campos com os nomes do firmware ANTIGO (a ponte do Pi le estes) ---
  JsonArray tof = doc["tof"].to<JsonArray>();
  // O robo nao tem ToF: os quatro ficam em -1, que e o "nao sei" que a ponte
  // ja traduz para None. Zero seria "obstaculo colado" - mentira perigosa.
  for (int i = 0; i < 4; i++) tof.add(-1);
  doc["obstacle_distance"] = -1;
  doc["passos_esq"] = motores.passosAcumuladosEsquerda();
  doc["passos_dir"] = motores.passosAcumuladosDireita();
  doc["rssi"] = WiFi.RSSI();
  doc["pan_us"] = panUs;
  doc["tilt_us"] = tiltUs;

  // --- campos que o firmware antigo NAO tinha ---
  doc["estado"] = motores.emMovimento() ? "MOVING" : "IDLE";
  doc["em_movimento"] = motores.emMovimento();
  doc["safe_mode"] = seguranca.pararAtivo();

  // Bussola: so publica rumo quando ele VALE. Um rumo invalido publicado
  // como 0.0 seria indistinguivel de "apontado para o norte".
  doc["bussola_calibrada"] = bussola.calibrada();
  if (bussola.rumoValido()) {
    doc["rumo_graus"] = bussola.rumoGraus();
  }

  if (dht.leituraValida()) {
    doc["temperatura_c"] = dht.temperaturaC();
    doc["umidade_percent"] = dht.umidadePercent();
  }

  String saida;
  serializeJson(doc, saida);
  servidor.broadcastTXT(saida);
}

void avisarMovimentoConcluido() {
  JsonDocument doc;
  doc["evento"] = "movimento_concluido";
  doc["passos_esq"] = motores.passosAcumuladosEsquerda();
  doc["passos_dir"] = motores.passosAcumuladosDireita();
  String saida;
  serializeJson(doc, saida);
  servidor.broadcastTXT(saida);
}

// --------------------------------------------------------------- comandos
void executarComando(uint8_t cliente, JsonDocument& doc) {
  const char* cmd = doc["cmd"] | "";
  const char* id = doc["id"] | "";

  // Qualquer comando valido conta como sinal de vida para o watchdog -
  // inclusive `ping`. E o que permite ao Pi segurar o robo andando sem
  // reenviar `drive` o tempo todo.
  ultimoComandoMs = millis();

  if (strcmp(cmd, "ping") == 0) {
    JsonDocument r;
    r["pong"] = true;  // compat: o firmware antigo respondia exatamente isto
    if (id[0] != '\0') r["ack"] = id;
    responder(cliente, r);
    return;
  }

  if (strcmp(cmd, "stop") == 0) {
    motores.parar();
    responderAck(cliente, id, true);
    return;
  }

  // `drive`: velocidade continua por lado, -255..255 (a escala do firmware
  // antigo - ESCALA_DRIVE na ponte do Pi).
  if (strcmp(cmd, "drive") == 0) {
    const int esq = doc["left"] | 0;
    const int dir = doc["right"] | 0;
    if (esq == 0 && dir == 0) {
      motores.parar();
      responderAck(cliente, id, true);
      return;
    }
    const float velocidade = (abs(esq) + abs(dir)) / 2.0f / 255.0f * 100.0f;
    // Lados opostos = giro no proprio eixo; mesmo sinal = frente ou re.
    if ((esq < 0) != (dir < 0)) {
      motores.girarContinuo(/*sentidoHorario=*/esq > 0, velocidade);
    } else if (esq < 0) {
      // ATENCAO: `andarFrente` IGNORA o sinal da velocidade - ela so diz o
      // quanto, nunca o lado. Passar um percentual negativo ali nao anda de
      // re, anda para frente com um numero sem sentido. Por isso existe o
      // `andarRe` (adicionado junto com este firmware).
      motores.andarRe(velocidade);
    } else {
      motores.andarFrente(velocidade);
    }
    responderAck(cliente, id, true);
    return;
  }

  // NOVO: distancia finita, resolvida DENTRO do ESP32.
  if (strcmp(cmd, "move") == 0) {
    const float cm = doc["cm"] | 0.0f;
    const float vel = doc["vel"] | 50.0f;
    if (cm == 0.0f) {
      responderAck(cliente, id, false, "cm ausente ou zero");
      return;
    }
    motores.andarDistancia(cm, vel);
    responderAck(cliente, id, true);
    return;
  }

  // NOVO: giro finito, idem.
  if (strcmp(cmd, "turn") == 0) {
    const float graus = doc["graus"] | 0.0f;
    const float vel = doc["vel"] | 40.0f;
    if (graus == 0.0f) {
      responderAck(cliente, id, false, "graus ausente ou zero");
      return;
    }
    motores.girarGraus(fabsf(graus), /*sentidoHorario=*/graus > 0, vel);
    responderAck(cliente, id, true);
    return;
  }

  if (strcmp(cmd, "gimbal") == 0) {
    bool mexeu = false;
    if (!doc["pan"].isNull()) {
      panUs = grausParaUs(doc["pan"].as<float>());
      servoPan.writeMicroseconds(panUs);
      mexeu = true;
    }
    if (!doc["tilt"].isNull()) {
      tiltUs = grausParaUs(doc["tilt"].as<float>());
      servoTilt.writeMicroseconds(tiltUs);
      mexeu = true;
    }
    responderAck(cliente, id, mexeu, mexeu ? nullptr : "nenhum eixo informado");
    return;
  }

  if (strcmp(cmd, "lanterna") == 0) {
    const bool acesa = doc["on"] | false;
    digitalWrite(pinos::RELE_LANTERNA, acesa ? pinos::LANTERNA_ACESA
                                             : pinos::LANTERNA_APAGADA);
    responderAck(cliente, id, true);
    return;
  }

  responderAck(cliente, id, false, "comando desconhecido");
}

void aoEvento(uint8_t cliente, WStype_t tipo, uint8_t* payload, size_t tamanho) {
  switch (tipo) {
    case WStype_CONNECTED:
      Serial.printf("[WS] cliente %u conectado\n", cliente);
      break;
    case WStype_DISCONNECTED:
      Serial.printf("[WS] cliente %u saiu\n", cliente);
      // NAO para os motores aqui: o Pi pode reconectar em menos de um
      // segundo, e frear a cada oscilacao de WiFi deixaria o robo aos
      // trancos. Quem para e o watchdog, que mede SILENCIO e nao socket.
      break;
    case WStype_TEXT: {
      JsonDocument doc;
      if (deserializeJson(doc, payload, tamanho)) {
        responderAck(cliente, nullptr, false, "json invalido");
        return;
      }
      executarComando(cliente, doc);
      break;
    }
    default:
      break;
  }
}

void conectarWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_SENHA);
  Serial.print("Conectando ao WiFi");
  // NAO bloqueia para sempre: sem rede o robo ainda tem de bootar, ficar
  // seguro e responder pelo monitor serial. Quem reconecta depois e o
  // proprio WiFi.setAutoReconnect.
  const unsigned long limite = millis() + 20000;
  while (WiFi.status() != WL_CONNECTED && millis() < limite) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    WiFi.setAutoReconnect(true);
    Serial.printf("WiFi ok! IP: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("Corpo ORION pronto! ws://%s:%d\n",
                  WiFi.localIP().toString().c_str(), WS_PORTA);
  } else {
    Serial.println("SEM WIFI - o corpo sobe mudo, motores travados pelo watchdog");
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);

  // Lanterna: nivel seguro ANTES do pinMode, senao o rele (ativo em LOW)
  // acende sozinho na janela do boot - GPIO2 nasce em LOW por ser strapping.
  digitalWrite(pinos::RELE_LANTERNA, pinos::LANTERNA_APAGADA);
  pinMode(pinos::RELE_LANTERNA, OUTPUT);
  digitalWrite(pinos::RELE_LANTERNA, pinos::LANTERNA_APAGADA);

  motores.iniciar();  // ja cuida do rele dos motores em estado seguro

  servoPan.attach(pinos::SERVO_PAN);
  servoTilt.attach(pinos::SERVO_TILT);
  servoPan.writeMicroseconds(panUs);
  servoTilt.writeMicroseconds(tiltUs);

  bussola.iniciar();  // tolera ausencia: `conectado()` fica false e so
  dht.iniciar();

  conectarWifi();

  servidor.begin();
  servidor.onEvent(aoEvento);
  ultimoComandoMs = millis();
}

void loop() {
  servidor.loop();

  // PID das duas rodas com o dt real. Tem de rodar a cada volta do loop -
  // e o unico lugar onde `move`/`turn` percebem que chegaram.
  motores.atualizar();

  // Watchdog: silencio longo demais para o robo continuar andando.
  if (!seguranca.avaliar(motores, ultimoComandoMs)) {
    // `avaliar` ja parou os motores; aqui so se registra.
  }

  const unsigned long agora = millis();
  if (motores.emMovimento() && (agora - ultimoComandoMs) > TIMEOUT_COMANDO_MS) {
    Serial.println("[WATCHDOG] sem comando - parando");
    motores.parar();
    ultimoComandoMs = agora;  // nao repete o aviso a cada volta
  }

  // Borda de "acabou o movimento": avisa UMA vez quem pediu.
  const bool andando = motores.emMovimento();
  if (estavaEmMovimento && !andando) {
    avisarMovimentoConcluido();
  }
  estavaEmMovimento = andando;

  // DHT so le com o robo parado (a leitura bloqueia ~25 ms e nao pode
  // atrasar o PID no meio de uma manobra).
  dht.atualizarSeParado(!andando);
  bussola.atualizar();

  if (agora - ultimaTelemetriaMs >= INTERVALO_TELEMETRIA_MS) {
    ultimaTelemetriaMs = agora;
    publicarTelemetria();
  }
}
