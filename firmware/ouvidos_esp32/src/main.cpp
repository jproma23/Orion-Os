// OUVIDOS DO FOFAO - dois microfones I2S transmitidos por WiFi.
//
// O QUE ESTE FIRMWARE FAZ, E POR QUE TAO POUCO
// --------------------------------------------
// Ele so le os dois microfones e despeja as amostras num socket TCP. Nada
// de FFT, nada de decidir direcao aqui. A conta de "de onde veio a voz"
// mora no Notebook (orion.voice.localizacao), junto com o Whisper - assim
// o MESMO fluxo de audio serve para as duas coisas, e o ESP32 fica com o
// trabalho que so ele pode fazer: estar perto dos microfones.
//
// PINOS - descobertos por medicao em 2026-07-28, nao por memoria
// --------------------------------------------------------------
//   SCK/BCLK = GPIO 26
//   WS/LRCL  = GPIO 25
//   SD/DADOS = GPIO 33
// A busca foi necessaria porque os valores lembrados nao funcionavam (e um
// deles, GPIO 24, nem existe no ESP32 - a numeracao tem buracos: 20, 24 e
// 28-31 nao saem do encapsulamento, e 6-11 sao da flash). Confirmado com
// 512/512 amostras nao-zero e os dois canais reagindo a voz; qualquer
// outra combinacao dava zero absoluto.
//
// FORMATO NA REDE
// ---------------
// int16 little-endian, intercalado esquerda/direita, 16000 Hz. Isso da
// 64 KB/s - trivial para WiFi. Os microfones entregam 24 bits uteis dentro
// de 32; ficamos com os 16 mais significativos, que e de sobra para voz e
// para a correlacao cruzada.
//
// O WiFi usa as credenciais JA GRAVADAS na NVS pelo WiFiManager do firmware
// original (rede KAMALL). Por isso WiFi.begin() vai sem argumentos: as
// gravacoes de aplicacao nao apagam a particao nvs em 0x9000.
#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <driver/i2s.h>

namespace {

constexpr int PINO_SCK = 26;
constexpr int PINO_WS = 25;
constexpr int PINO_SD = 33;

constexpr int TAXA_HZ = 16000;
constexpr i2s_port_t PORTA_I2S = I2S_NUM_0;
constexpr uint16_t PORTA_TCP = 9100;
constexpr int AMOSTRAS = 256;  // por canal, por leitura = 16 ms

WiFiServer servidor(PORTA_TCP);
WiFiClient cliente;

int32_t cru[AMOSTRAS * 2];
int16_t saida[AMOSTRAS * 2];

bool configurarI2S() {
  i2s_config_t cfg = {};
  cfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = TAXA_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = AMOSTRAS;
  cfg.use_apll = false;
  if (i2s_driver_install(PORTA_I2S, &cfg, 0, nullptr) != ESP_OK) return false;

  i2s_pin_config_t p = {};
  p.bck_io_num = PINO_SCK;
  p.ws_io_num = PINO_WS;
  p.data_out_num = I2S_PIN_NO_CHANGE;
  p.data_in_num = PINO_SD;
  if (i2s_set_pin(PORTA_I2S, &p) != ESP_OK) {
    i2s_driver_uninstall(PORTA_I2S);
    return false;
  }
  i2s_zero_dma_buffer(PORTA_I2S);
  return true;
}

}  // namespace

// 2026-10-31: 13 -> 12 (2x). Medido com o mic novo: o dono dizendo
// "Jarvis" chegou a rms 0,0064 (0,6% da escala) e o Whisper DESCARTOU a
// palavra por confianca baixa (-1,27). Silencio ~170/32768; um bipe de
// teste no alto-falante a ~8500 rms - com 2x fica ~17000, ainda sem saturar.
static const int DESLOCAMENTO_GANHO = 12;

void setup() {
  Serial.begin(115200);
  delay(700);
  Serial.println();
  Serial.println("==================================================");
  Serial.println("  OUVIDOS DO FOFAO - streaming de audio I2S");
  Serial.printf("  SCK=%d  WS=%d  SD=%d   %d Hz estereo\n",
                PINO_SCK, PINO_WS, PINO_SD, TAXA_HZ);
  Serial.println("==================================================");

  if (!configurarI2S()) {
    Serial.println("PAROU: I2S nao inicializou.");
    return;
  }
  Serial.println("[I2S] ok");

  // credenciais ja gravadas na NVS pelo firmware anterior
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("ouvidos");
  WiFi.begin();
  Serial.print("[WiFi] conectando");
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] NAO conectou - credenciais da NVS podem ter sumido.");
    Serial.println("       O audio so sai pela rede; sem WiFi nao ha ouvidos.");
    return;
  }
  Serial.printf("[WiFi] conectado - IP %s\n", WiFi.localIP().toString().c_str());
  if (MDNS.begin("ouvidos")) {
    MDNS.addService("orion-audio", "tcp", PORTA_TCP);
    Serial.println("[mDNS] ouvidos.local");
  }

  // OTA (2026-10-31): o dono tem UM cabo USB e ele fica no corpo. Todo
  // firmware dos ouvidos TEM que trazer isto, senao a proxima gravacao
  // volta a exigir o cabo. Gravar: `pio run -e ota -t upload`.
  ArduinoOTA.setHostname("ouvidos");
  ArduinoOTA.begin();
  Serial.println("[OTA] pronto (UDP 3232)");

  servidor.begin();
  servidor.setNoDelay(true);
  Serial.printf("[TCP] ouvindo na porta %d\n", PORTA_TCP);
  Serial.println("Formato: int16 LE, intercalado esq/dir, 16000 Hz.");
}

void loop() {
  ArduinoOTA.handle();
  if (!cliente || !cliente.connected()) {
    WiFiClient novo = servidor.available();
    if (novo) {
      cliente = novo;
      cliente.setNoDelay(true);
      Serial.printf("[TCP] cliente conectado: %s\n",
                    cliente.remoteIP().toString().c_str());
    } else {
      // sem ninguem escutando: nao adianta gastar CPU lendo o I2S
      delay(50);
      return;
    }
  }

  size_t lidos = 0;
  if (i2s_read(PORTA_I2S, cru, sizeof(cru), &lidos, pdMS_TO_TICKS(200)) != ESP_OK) return;
  const int total = lidos / sizeof(int32_t);
  if (total <= 0) return;

  // GANHO DIGITAL - era `>> 16`, medido e corrigido em 2026-07-29.
  //
  // Com `>> 16` ficavam so os 16 bits de cima dos 24 uteis. Medido ao vivo
  // com o dono falando:
  //
  //     piso de ruido : 0,082% da escala
  //     pico de voz   : 0,324% da escala   (relacao voz/ruido 4,0x)
  //
  // Os mics discriminam a voz muito bem - o problema era so nivel. A
  // referencia e o mic USB do Notebook, que o Whisper transcreve a ~7%:
  // faltavam ~22x.
  //
  // `>> 12` da 16x: leva a voz medida a ~5,2% e o pico de 0,0225 para 0,36 da
  // escala, com folga. `>> 11` (32x) chegaria a 10%, mas poria o pico em 72%
  // e um grito estouraria.
  //
  // O LIMITE ABAIXO E OBRIGATORIO, nao zelo: sem ele um som alto passa de
  // 32767 e o cast para int16_t DOBRA o valor (vira negativo). Isso soa muito
  // pior que audio baixo - troca-se sinal fraco por estalo. Melhor saturar
  // que inverter.
  for (int i = 0; i < total; i++) {
    int32_t v = cru[i] >> DESLOCAMENTO_GANHO;
    if (v > 32767) v = 32767;
    else if (v < -32768) v = -32768;
    saida[i] = static_cast<int16_t>(v);
  }

  const size_t bytes = total * sizeof(int16_t);
  if (cliente.write(reinterpret_cast<uint8_t*>(saida), bytes) != bytes) {
    Serial.println("[TCP] cliente caiu no meio do envio");
    cliente.stop();
  }
}
