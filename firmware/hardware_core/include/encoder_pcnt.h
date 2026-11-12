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

#include <freertos/FreeRTOS.h>

namespace orion {

class EncoderPcnt {
 public:
  // unidade: unidade PCNT dedicada a esta roda (0 a 7). pino: canal A
  // (pins.h: ENCODER_ESQ=14, ENCODER_DIR=34).
  void iniciar(int unidade, int pino);

  // Contagem BRUTA acumulada (sempre crescente, nunca decrementa - canal
  // unico nao sabe sentido). O chamador aplica o sinal externamente.
  //
  // LEITURA CONSISTENTE (auditoria 12/11). O total e `_acumulado` (somado
  // pela ISR de overflow) + o contador do hardware, que volta a 0 ao chegar
  // em 30000. Lidos em dois passos e sem trava, a volta a zero entre eles -
  // ou a ISR ainda pendente - dava uma leitura com 30000 pulsos A MENOS e a
  // seguinte certa: para a odometria do Pi, metros para tras e para a frente
  // num piscar; para o PID, um pico falso de velocidade. Agora a leitura e
  // sob trava e, como a contagem so sobe, "total menor que o ultimo" e a
  // volta a zero cuja ISR ainda nao rodou.
  int64_t pulsos() const;

  void zerar();

  // Chamado pela ISR de overflow do PCNT - nao chamar de outro lugar.
  void _sobre_limite();

 private:
  int _unidade = -1;
  volatile int64_t _acumulado = 0;
  //: Ultimo total devolvido - a contagem nunca desce (ver pulsos()).
  mutable int64_t _ultimoTotal = 0;
  //: Protege _acumulado (ISR) e _ultimoTotal entre a ISR e o loop.
  mutable portMUX_TYPE _trava = portMUX_INITIALIZER_UNLOCKED;
};

}  // namespace orion
