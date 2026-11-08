// VARREDURA DE PINOS I2S - temporario (2026-10-25). Descobre em que pinos o
// microfone (INMP441) responde: testa SCK/WS/SD e imprime as combinacoes em que
// o SD traz amostras que VARIAM. Zero absoluto = nada ligado ali.
#include <Arduino.h>
#include <driver/i2s.h>

static const int SAIDAS[] = {4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33};
static const int ENTRADAS[] = {4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27,
                               32, 33, 34, 35, 36, 39};
static int32_t buf[256 * 2];

static bool instalar() {
  i2s_config_t cfg = {};
  cfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = 16000;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 4;
  cfg.dma_buf_len = 256;
  return i2s_driver_install(I2S_NUM_0, &cfg, 0, nullptr) == ESP_OK;
}

// Mede uma combinacao: fracao de amostras nao-zero e quantos valores
// distintos (um pino flutuando costuma dar tudo -1 ou tudo igual).
static void medir(int sck, int ws, int sd, float& naoZero, int& distintos, double& rms) {
  i2s_pin_config_t p = {};
  p.bck_io_num = sck;
  p.ws_io_num = ws;
  p.data_out_num = I2S_PIN_NO_CHANGE;
  p.data_in_num = sd;
  i2s_set_pin(I2S_NUM_0, &p);
  i2s_zero_dma_buffer(I2S_NUM_0);
  size_t lidos = 0;
  for (int i = 0; i < 3; i++) i2s_read(I2S_NUM_0, buf, sizeof(buf), &lidos, pdMS_TO_TICKS(100));
  int n = lidos / 4, nz = 0;
  int32_t vistos[16];
  int nv = 0;
  double soma2 = 0;
  for (int i = 0; i < n; i++) {
    int32_t v = buf[i] >> 8;
    if (v != 0) nz++;
    soma2 += (double)v * v;
    bool novo = true;
    for (int k = 0; k < nv; k++) if (vistos[k] == v) { novo = false; break; }
    if (novo && nv < 16) vistos[nv++] = v;
  }
  naoZero = n ? (float)nz / n : 0;
  distintos = nv;
  rms = n ? sqrt(soma2 / n) / 8388608.0 : 0;
}

void setup() {
  Serial.begin(115200);
  delay(800);
  Serial.println("\n=== VARREDURA I2S ===");
  if (!instalar()) { Serial.println("I2S nao instalou"); return; }
  int achados = 0;
  for (int sck : SAIDAS) for (int ws : SAIDAS) {
    if (ws == sck) continue;
    for (int sd : ENTRADAS) {
      if (sd == sck || sd == ws) continue;
      float nz; int dist; double rms;
      medir(sck, ws, sd, nz, dist, rms);
      if (nz > 0.5f && dist >= 12) {
        Serial.printf("ACHADO SCK=%d WS=%d SD=%d  naozero=%.0f%% distintos=%d rms=%.5f\n",
                      sck, ws, sd, nz * 100, dist, rms);
        achados++;
      }
    }
    Serial.printf("# sck=%d ws=%d ok\n", sck, ws);
  }
  Serial.printf("=== FIM: %d combinacoes com sinal ===\n", achados);
}

void loop() { delay(1000); }
