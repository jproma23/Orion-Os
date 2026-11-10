// Leitura de encoder por PCNT (periferico de hardware do ESP32) - CANAL
// UNICO por roda (2026-09-13), nao quadratura completa.
//
// Por que canal unico (o MOTIVO CAIU, corrigido 2026-11-10): os pinos que
// sobrariam para o canal B esbarravam nos ECHO dos ultrassons, e esses
// sairam do robo em 2026-09-19. Este comentario dizia que os encoders
// moravam nos "GPIO 36 e 39" - a placa do dono NAO TEM esses pinos
// (2026-10-25). Fiacao real (pins.h): esquerdo A=14/B=27, direito A=34/B=35.
// O canal B esta ligado e nao e lido: quadratura completa e tarefa nao
// feita, nao impossibilidade.
//
// Consequencia: este encoder NAO sabe o sentido de giro sozinho (isso
// exigiria o segundo canal). O MotorManager aplica o sinal (+/-) usando o
// que foi COMANDADO pro motor - correto enquanto o motor obedece ao
// comando; se o motor travar/patinar ao contrario, a leitura de velocidade
// fica errada naquele instante. Aceitavel para o Gate 1; revisar se virar
// problema na bancada.
#pragma once

#include <stdint.h>

namespace orion {

class EncoderPcnt {
 public:
  // unidade: unidade PCNT dedicada a esta roda (0 a 7). pino: canal A
  // (pins.h: ENCODER_ESQ=14, ENCODER_DIR=34).
  void iniciar(int unidade, int pino);

  // Contagem BRUTA acumulada (sempre crescente, nunca decrementa - canal
  // unico nao sabe sentido). O chamador aplica o sinal externamente.
  int64_t pulsos() const;

  void zerar();

  // Chamado pela ISR de overflow do PCNT - nao chamar de outro lugar.
  void _sobre_limite();

 private:
  int _unidade = -1;
  volatile int64_t _acumulado = 0;
};

}  // namespace orion
