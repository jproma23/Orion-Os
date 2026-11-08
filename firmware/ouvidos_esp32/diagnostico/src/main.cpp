// DIAGNOSTICO DO MIC I2S (2026-10-31) - com WiFi + OTA para nunca mais
// depender do cabo USB (o dono tem um cabo so, e ele fica no corpo).
//
// Le o I2S nos pinos de julho (SCK 26, WS 25, SD 33) e alterna o resistor
// interno do SD a cada ~3 s: PULL-UP, PULL-DOWN, nenhum.
//   - SD solto / mic sem energia: a leitura SEGUE o resistor
//     (tudo 1 com pull-up, tudo 0 com pull-down)
//   - mic vivo: valores variando, parecidos nos tres modos
// O resultado sai na serial E em TCP 9101 (texto; `nc ouvidos.local 9101`).
// OTA: ArduinoOTA com hostname "ouvidos" (UDP 3232).
#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <driver/i2s.h>

constexpr int SCK_ = 26, WS_ = 25, SD_ = 33;
int32_t buf[512];
WiFiServer srv(9101);
WiFiClient cli;

void say(const char* s) {
  Serial.print(s);
  if (cli && cli.connected()) cli.print(s);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== DIAG MIC I2S + OTA ===");
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("ouvidos");
  WiFi.begin();  // credenciais ja gravadas na NVS
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(500);
  Serial.printf("WiFi=%d IP=%s\n", WiFi.status(), WiFi.localIP().toString().c_str());
  MDNS.begin("ouvidos");
  ArduinoOTA.setHostname("ouvidos");
  ArduinoOTA.begin();
  srv.begin();

  i2s_config_t c = {};
  c.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  c.sample_rate = 16000;
  c.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  c.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  c.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  c.dma_buf_count = 8; c.dma_buf_len = 256;
  Serial.printf("install=%d\n", i2s_driver_install(I2S_NUM_0, &c, 0, nullptr));
  i2s_pin_config_t p = {};
  p.bck_io_num = SCK_; p.ws_io_num = WS_;
  p.data_out_num = I2S_PIN_NO_CHANGE; p.data_in_num = SD_;
  Serial.printf("set_pin=%d\n", i2s_set_pin(I2S_NUM_0, &p));
}

void loop() {
  static int modo = 0, rep = 0;
  static uint32_t prox = 0;
  const char* nome[] = {"PULL-UP", "PULL-DOWN", "SEM RESISTOR"};
  ArduinoOTA.handle();
  if (!cli || !cli.connected()) { WiFiClient n = srv.available(); if (n) cli = n; }
  if (millis() < prox) { delay(5); return; }
  prox = millis() + 1000;

  if (rep == 0) {
    gpio_set_pull_mode((gpio_num_t)SD_, modo == 0 ? GPIO_PULLUP_ONLY :
                       modo == 1 ? GPIO_PULLDOWN_ONLY : GPIO_FLOATING);
    delay(30);
    i2s_zero_dma_buffer(I2S_NUM_0);
  }
  size_t n = 0;
  i2s_read(I2S_NUM_0, buf, sizeof(buf), &n, pdMS_TO_TICKS(500));
  int tot = n / 4, nz[2] = {0, 0}, ff[2] = {0, 0};
  int32_t mn[2] = {INT32_MAX, INT32_MAX}, mx[2] = {INT32_MIN, INT32_MIN};
  for (int i = 0; i < tot; i++) {
    int ch = i & 1; int32_t v = buf[i];
    if (v) nz[ch]++;
    if ((v >> 8) == -1) ff[ch]++;
    v >>= 8; if (v < mn[ch]) mn[ch] = v; if (v > mx[ch]) mx[ch] = v;
  }
  char l[300];
  snprintf(l, sizeof(l),
    "[%-12s] n=%d | A naozero=%d tudo1=%d min=%ld max=%ld | B naozero=%d tudo1=%d min=%ld max=%ld | cru %08lx %08lx %08lx %08lx\n",
    nome[modo], tot, nz[0], ff[0], (long)mn[0], (long)mx[0], nz[1], ff[1], (long)mn[1], (long)mx[1],
    (unsigned long)buf[0], (unsigned long)buf[1], (unsigned long)buf[2], (unsigned long)buf[3]);
  say(l);
  if (++rep == 3) { rep = 0; modo = (modo + 1) % 3; }
}
