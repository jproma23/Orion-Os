#include "velocity_pid.h"

#include <math.h>

namespace orion {

float VelocidadePid::atualizar(float alvo_rad_s, float medido_rad_s, float dt_s) {
  if (dt_s <= 0.0f) {
    return 0.0f;
  }

  float erro = alvo_rad_s - medido_rad_s;

  _integral += erro * dt_s * _ganhos.ki;
  if (_integral > _ganhos.integral_max) _integral = _ganhos.integral_max;
  if (_integral < -_ganhos.integral_max) _integral = -_ganhos.integral_max;

  float feedforward = 0.0f;
  if (alvo_rad_s > 0.0f) {
    feedforward = _ganhos.kff_zona_morta;
  } else if (alvo_rad_s < 0.0f) {
    feedforward = -_ganhos.kff_zona_morta;
  }

  float saida = feedforward + _ganhos.kp * erro + _integral;

  if (saida > 1.0f) saida = 1.0f;
  if (saida < -1.0f) saida = -1.0f;
  return saida;
}

void VelocidadePid::zerar() {
  _integral = 0.0f;
}

}  // namespace orion
