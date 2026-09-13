// Leitura de encoder por PCNT (periferico de hardware do ESP32) - CANAL
// UNICO por roda (2026-09-13), nao quadratura completa.
//
// Por que canal unico: os dois pinos que sobrariam pra quadratura completa
// (A+B por roda) esbarravam nos ECHO dos ultrassons - um deles (frontal,
// GPIO34) alimenta o SafetyManager de verdade (parada por obstaculo). Nao
// vale a pena arriscar seguranca por resolucao de encoder. Os UNICOS dois
// pinos input-only totalmente livres nesta montagem (GPIO 36 e 39) bastam
// pra 1 canal por roda.
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
  // unidade: unidade PCNT dedicada a esta roda (0 a 7). pino: unico canal
  // de pulso (input-only: GPIO 36 ou 39 nesta montagem).
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
