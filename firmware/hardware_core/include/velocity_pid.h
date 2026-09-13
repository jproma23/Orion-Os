// PID de velocidade por roda, alvo em rad/s - mesmo principio do
// firmware/motor_core (EDR-0031), reaproveitado aqui porque o motor DC
// entrou neste ESP32 (decisao do dono, 2026-09-13).
//
// GANHOS SAO PLACEHOLDER (sem bancada - motor/encoder ainda nao chegaram).
// NAO confiar nestes valores sem medir de verdade no Gate 1.
#pragma once

namespace orion {

struct GanhosPid {
  float kp = 0.6f;
  float ki = 0.15f;
  float kff_zona_morta = 0.12f;
  float integral_max = 0.5f;
};

class VelocidadePid {
 public:
  explicit VelocidadePid(const GanhosPid& ganhos = GanhosPid{}) : _ganhos(ganhos) {}

  float atualizar(float alvo_rad_s, float medido_rad_s, float dt_s);
  void zerar();

 private:
  GanhosPid _ganhos;
  float _integral = 0.0f;
};

}  // namespace orion
