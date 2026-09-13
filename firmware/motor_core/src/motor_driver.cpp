#include "motor_driver.h"

#include <Arduino.h>

namespace orion {

namespace {
constexpr uint32_t PWM_FREQ_HZ = 20000;  // acima do audivel - L298N nao chia
constexpr uint8_t PWM_RESOLUCAO_BITS = 10;  // duty 0-1023
constexpr uint32_t PWM_DUTY_MAX = (1u << PWM_RESOLUCAO_BITS) - 1;
}  // namespace

void MotorDriver::iniciar(int in1, int in2, int pino_pwm, int canal_ledc) {
  _in1 = in1;
  _in2 = in2;
  _canal_ledc = canal_ledc;

  pinMode(_in1, OUTPUT);
  pinMode(_in2, OUTPUT);
  digitalWrite(_in1, LOW);
  digitalWrite(_in2, LOW);

  ledcSetup(_canal_ledc, PWM_FREQ_HZ, PWM_RESOLUCAO_BITS);
  ledcAttachPin(pino_pwm, _canal_ledc);
  ledcWrite(_canal_ledc, 0);
}

void MotorDriver::definir(float velocidade) {
  if (velocidade > 1.0f) velocidade = 1.0f;
  if (velocidade < -1.0f) velocidade = -1.0f;

  digitalWrite(_in1, velocidade >= 0 ? HIGH : LOW);
  digitalWrite(_in2, velocidade >= 0 ? LOW : HIGH);

  uint32_t duty = static_cast<uint32_t>(fabsf(velocidade) * PWM_DUTY_MAX);
  ledcWrite(_canal_ledc, duty);
}

void MotorDriver::parar() {
  digitalWrite(_in1, LOW);
  digitalWrite(_in2, LOW);
  ledcWrite(_canal_ledc, 0);
}

}  // namespace orion
