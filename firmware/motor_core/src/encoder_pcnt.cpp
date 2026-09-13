#include "encoder_pcnt.h"

#include <driver/pcnt.h>

namespace orion {

namespace {
constexpr int16_t LIMITE_ALTO = 30000;
constexpr int16_t LIMITE_BAIXO = -30000;

// O servico de ISR do PCNT e global (compartilhado por todas as unidades) -
// so pode ser instalado uma vez em todo o programa. As duas rodas chamam
// iniciar() em sequencia; a segunda chamada bate no ESP_ERR_INVALID_STATE
// e e ignorada de proposito.
bool g_servico_isr_instalado = false;

// A ISR de overflow do PCNT so recebe um ponteiro de usuario (void*) -
// guardamos o ponteiro do objeto e a unidade junto num par estatico
// pequeno, ja que sao poucas unidades (no maximo 8, EDR-0031 usa 2).
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
    ctx->encoder->_sobre_limite(1);
  }
  if (status & PCNT_EVT_L_LIM) {
    ctx->encoder->_sobre_limite(-1);
  }
}
}  // namespace

void EncoderPcnt::iniciar(int unidade, int pino_a, int pino_b) {
  _unidade = unidade;
  pcnt_unit_t u = static_cast<pcnt_unit_t>(unidade);

  // Canal 0: pulso = A, controle = B. Decodifica metade das transicoes.
  pcnt_config_t canal_a = {};
  canal_a.pulse_gpio_num = pino_a;
  canal_a.ctrl_gpio_num = pino_b;
  canal_a.channel = PCNT_CHANNEL_0;
  canal_a.unit = u;
  canal_a.pos_mode = PCNT_COUNT_DEC;
  canal_a.neg_mode = PCNT_COUNT_INC;
  canal_a.lctrl_mode = PCNT_MODE_KEEP;
  canal_a.hctrl_mode = PCNT_MODE_REVERSE;
  canal_a.counter_h_lim = LIMITE_ALTO;
  canal_a.counter_l_lim = LIMITE_BAIXO;
  pcnt_unit_config(&canal_a);

  // Canal 1: pulso = B, controle = A (cruzado) - completa a decodificacao
  // 4x (conta as 4 transicoes de um ciclo de quadratura, nao so 1).
  pcnt_config_t canal_b = {};
  canal_b.pulse_gpio_num = pino_b;
  canal_b.ctrl_gpio_num = pino_a;
  canal_b.channel = PCNT_CHANNEL_1;
  canal_b.unit = u;
  canal_b.pos_mode = PCNT_COUNT_INC;
  canal_b.neg_mode = PCNT_COUNT_DEC;
  canal_b.lctrl_mode = PCNT_MODE_REVERSE;
  canal_b.hctrl_mode = PCNT_MODE_KEEP;
  canal_b.counter_h_lim = LIMITE_ALTO;
  canal_b.counter_l_lim = LIMITE_BAIXO;
  pcnt_unit_config(&canal_b);

  // Filtro de glitch (ruido de contato/EMI da escova do motor DC) - 1000
  // ciclos de APB (80MHz) = 12.5us, curto o bastante pra nao comer pulso
  // real num encoder de poucos kHz.
  pcnt_set_filter_value(u, 1000);
  pcnt_filter_enable(u);

  pcnt_counter_pause(u);
  pcnt_counter_clear(u);

  pcnt_event_enable(u, PCNT_EVT_H_LIM);
  pcnt_event_enable(u, PCNT_EVT_L_LIM);

  g_contexto[unidade] = {this, u};
  if (!g_servico_isr_instalado) {
    pcnt_isr_service_install(0);
    g_servico_isr_instalado = true;
  }
  pcnt_isr_handler_add(u, tratar_overflow, &g_contexto[unidade]);

  pcnt_counter_resume(u);
}

int64_t EncoderPcnt::ticks() const {
  if (_unidade < 0) {
    return 0;
  }
  int16_t atual = 0;
  pcnt_get_counter_value(static_cast<pcnt_unit_t>(_unidade), &atual);
  return _acumulado + static_cast<int64_t>(atual);
}

void EncoderPcnt::zerar() {
  if (_unidade < 0) {
    return;
  }
  pcnt_counter_clear(static_cast<pcnt_unit_t>(_unidade));
  _acumulado = 0;
}

void EncoderPcnt::_sobre_limite(int16_t direcao) {
  _acumulado += static_cast<int64_t>(direcao) * LIMITE_ALTO;
}

}  // namespace orion
