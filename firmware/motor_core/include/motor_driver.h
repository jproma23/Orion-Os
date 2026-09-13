// Driver da ponte H L298N (EDR-0031: hardware que o dono ja tem).
//
// PWM em hardware (LEDC), nunca analogWrite "generico" - o LEDC nao disputa
// timer com mais nada neste firmware (mesmo motivo do ESP32Servo no
// hardware_core usar LEDC por baixo).
//
// ATENCAO DE BANCADA (EDR-0031, nao e coisa de firmware): para este PWM
// funcionar de verdade nesta placa L298N e preciso ter removido o jumper
// ENA/ENB (senao o motor so liga/desliga) e o jumper "ATIVA 5V" (senao o
// regulador da placa nao entrega os ~7V que a logica do L298N precisa) -
// ver RETOMADA_MIGRACAO.md.
#pragma once

namespace orion {

class MotorDriver {
 public:
  // in1/in2: pinos de sentido. pino_pwm: ENA ou ENB (velocidade).
  // canal_ledc: canal do LEDC (0-15) - cada motor precisa de um canal
  // proprio, nunca compartilhado.
  void iniciar(int in1, int in2, int pino_pwm, int canal_ledc);

  // velocidade: -1.0 (re, maximo) a +1.0 (frente, maximo). Zero para.
  void definir(float velocidade);

  // Corta o motor imediatamente (freio por curto, IN1=IN2=HIGH seria mais
  // agressivo; aqui usamos parada simples IN1=IN2=LOW + PWM=0, suficiente
  // para o watchdog do EDR-0031).
  void parar();

 private:
  int _in1 = -1;
  int _in2 = -1;
  int _canal_ledc = -1;
};

}  // namespace orion
