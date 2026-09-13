// PID de velocidade por roda, alvo em rad/s (EDR-0031).
//
// Termo integral e feedforward existem porque motor com reducao tem zona
// morta: abaixo de um certo PWM a roda simplesmente nao sai do lugar, e
// velocidade baixa e exatamente o regime que o Regulated Pure Pursuit usa
// (F5). Sem feedforward, o P sozinho demora a vencer a zona morta e o
// robo "engasga" antes de andar.
//
// GANHOS SAO PLACEHOLDER (ainda sem bancada - motor/encoder nao chegaram).
// NAO validar nem confiar nestes valores sem medir de verdade no Gate 1
// (2m em linha reta, erro < 5%; 360 no lugar, erro de heading < 10%).
#pragma once

namespace orion {

struct GanhosPid {
  float kp = 0.6f;
  float ki = 0.15f;
  // feedforward: fracao minima de PWM (0-1) para vencer a zona morta,
  // aplicada com o SINAL do alvo assim que ele deixa de ser zero.
  float kff_zona_morta = 0.12f;
  float integral_max = 0.5f;  // anti-windup: limite do termo Ki*soma_erro
};

class VelocidadePid {
 public:
  explicit VelocidadePid(const GanhosPid& ganhos = GanhosPid{}) : _ganhos(ganhos) {}

  // alvo_rad_s / medido_rad_s: velocidade angular da RODA (nao do motor
  // antes da reducao). dt_s: tempo desde a ultima chamada, em segundos.
  // Retorna comando de -1.0 a +1.0 para MotorDriver::definir().
  float atualizar(float alvo_rad_s, float medido_rad_s, float dt_s);

  void zerar();

 private:
  GanhosPid _ganhos;
  float _integral = 0.0f;
};

}  // namespace orion
