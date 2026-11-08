#include "velocity_pid.h"

#include <math.h>

namespace orion {

float VelocidadePid::atualizar(float alvo_rad_s, float medido_rad_s, float dt_s)
{
  if (dt_s <= 0.0f) {
    return 0.0f;
  }

  float erro = alvo_rad_s - medido_rad_s;

  float integralAntes = _integral;
  _integral += erro * dt_s * _ganhos.ki;
  if (_integral > _ganhos.integral_max) _integral = _ganhos.integral_max;
  if (_integral < -_ganhos.integral_max) _integral = -_ganhos.integral_max;

  float feedforward = _ganhos.kff_ganho * alvo_rad_s;
  if (alvo_rad_s > 0.0f) {
    feedforward += _ganhos.kff_zona_morta;
  } else if (alvo_rad_s < 0.0f) {
    feedforward -= _ganhos.kff_zona_morta;
  }

  float saida = feedforward + _ganhos.kp * erro + _integral;

  // ANTI-WINDUP (2026-10-31): saturado e o erro empurrando para o MESMO lado,
  // a integral nao acumula. Sem isto, uma roda que passou segundos no maximo
  // sem alcancar o alvo (bateria fraca) guardava a integral cheia - e quando
  // a malha da reta pedia para ela frear, ela demorava segundos para obedecer.
  if ((saida > 1.0f && erro > 0.0f) || (saida < -1.0f && erro < 0.0f)) {
    _integral = integralAntes;
    saida = feedforward + _ganhos.kp * erro + _integral;
  }

  if (saida > 1.0f) saida = 1.0f;
  if (saida < -1.0f) saida = -1.0f;
  return saida;
}

void VelocidadePid::zerar()
{
  _integral = 0.0f;
}

}  // namespace orion
