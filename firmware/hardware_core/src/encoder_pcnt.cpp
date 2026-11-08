#include "encoder_pcnt.h"

#include <driver/gpio.h>
#include <driver/pcnt.h>

namespace orion {

namespace {
constexpr int16_t LIMITE_ALTO = 30000;

bool g_servico_isr_instalado = false;

struct ContextoIsr {
  EncoderPcnt* encoder;
  pcnt_unit_t unidade;
};
ContextoIsr g_contexto[PCNT_UNIT_MAX];

void IRAM_ATTR tratar_overflow(void* arg) {
  auto* ctx = static_cast<ContextoIsr*>(arg);
  uint32_t status = 0;
  pcnt_get_event_status(ctx->unidade, &status);
  if (status & PCNT_EVT_H_LIM) {
    ctx->encoder->_sobre_limite();
  }
}
}  // namespace

void EncoderPcnt::iniciar(int unidade, int pino)
{
  _unidade = unidade;
  pcnt_unit_t u = static_cast<pcnt_unit_t>(unidade);

  // Canal unico, sem controle de sentido (ctrl_gpio_num = -1): conta as
  // duas bordas do pulso (INC nas duas) para 2x de resolucao, sempre
  // crescente. O limite baixo fica bem negativo (nunca alcancado de
  // proposito) porque este canal nunca decrementa.
  pcnt_config_t canal = {};
  canal.pulse_gpio_num = pino;
  canal.ctrl_gpio_num = PCNT_PIN_NOT_USED;
  canal.channel = PCNT_CHANNEL_0;
  canal.unit = u;
  canal.pos_mode = PCNT_COUNT_INC;
  canal.neg_mode = PCNT_COUNT_INC;
  canal.lctrl_mode = PCNT_MODE_KEEP;
  canal.hctrl_mode = PCNT_MODE_KEEP;
  canal.counter_h_lim = LIMITE_ALTO;
  canal.counter_l_lim = 0;
  pcnt_unit_config(&canal);

  // Pull-up interno onde existe (GPIO < 34). Nos 34-39 ele nao existe e o
  // pedido e ignorado - la o resistor tem que ser externo (pins.h).
  if (pino < 34) {
    gpio_set_pull_mode(static_cast<gpio_num_t>(pino), GPIO_PULLUP_ONLY);
  }

  // Filtro de glitch (ruido de contato/EMI da escova do motor DC).
  pcnt_set_filter_value(u, 1000);
  pcnt_filter_enable(u);

  pcnt_counter_pause(u);
  pcnt_counter_clear(u);

  pcnt_event_enable(u, PCNT_EVT_H_LIM);

  g_contexto[unidade] = {this, u};
  if (!g_servico_isr_instalado) {
    pcnt_isr_service_install(0);
    g_servico_isr_instalado = true;
  }
  pcnt_isr_handler_add(u, tratar_overflow, &g_contexto[unidade]);

  pcnt_counter_resume(u);
}

int64_t EncoderPcnt::pulsos() const
{
  if (_unidade < 0) {
    return 0;
  }
  int16_t atual = 0;
  pcnt_get_counter_value(static_cast<pcnt_unit_t>(_unidade), &atual);
  return _acumulado + static_cast<int64_t>(atual);
}

void EncoderPcnt::zerar()
{
  if (_unidade < 0) {
    return;
  }
  pcnt_counter_clear(static_cast<pcnt_unit_t>(_unidade));
  _acumulado = 0;
}

void EncoderPcnt::_sobre_limite()
{
  _acumulado += LIMITE_ALTO;
}

}  // namespace orion
