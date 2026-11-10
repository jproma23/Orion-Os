// PID de velocidade por roda, alvo em rad/s - mesmo principio do
// firmware/motor_core (EDR-0031), reaproveitado aqui porque o motor DC
// entrou neste ESP32 (decisao do dono, 2026-09-13).
//
// GANHOS (bancada 2026-10-31, rodas no ar, bateria de 10 V): os de setembro
// eram palpite e a roda levava segundos para chegar no alvo - pedindo 5 cm/s
// ela fazia 3,6 depois de 1 s. Entrou um feedforward PROPORCIONAL (o PWM ja
// nasce perto do necessario: ~0,25 de duty por rad/s nesta bateria) e a
// integral ficou mais rapida. Reajustar com o robo no chao (carga).
#pragma once

namespace orion {

struct GanhosPid {
  float kp = 0.6f;
  float ki = 0.8f;
  float kff_zona_morta = 0.12f;
  float kff_ganho = 0.25f;   // duty por rad/s de alvo
  float integral_max = 0.6f;
};

class VelocidadePid {
 public:
  explicit VelocidadePid(const GanhosPid& ganhos = GanhosPid{}) : _ganhos(ganhos) {}

  // Saida em -saidaMaxima..+saidaMaxima (fracao de duty).
  float atualizar(float alvo_rad_s, float medido_rad_s, float dt_s);
  void zerar();

  // TETO DA SAIDA (2026-11-10, C4-02): quem decide e o motor_manager.h
  // (DUTY_MAX_FRACAO). Mora aqui, e nao so no ledcWrite, porque o
  // anti-windup precisa saber onde a saida PARA de crescer - com o teto so
  // no PWM, o PID acharia que ainda tinha folga ate 1,0 e enchia a integral.
  void definirSaidaMaxima(float saidaMaxima) { _saidaMaxima = saidaMaxima; }

 private:
  GanhosPid _ganhos;
  float _integral = 0.0f;
  float _saidaMaxima = 1.0f;
};

}  // namespace orion
