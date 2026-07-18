// Encoder Manager (Cap 10 secao 10).
//
// RESERVADO: pinos 18/19 nao tem encoder fisico ligado nesta montagem
// (confirmado com o usuario) - a contagem fica em zero ate os encoders
// serem instalados, sem crash nem comportamento indefinido. O Motor
// Manager ja fornece uma odometria aproximada por contagem de passos
// enquanto isso.
#pragma once

#include <Arduino.h>

namespace orion {

class EncoderManager {
 public:
  void iniciar();
  long pulsosEsquerdo() const;
  long pulsosDireito() const;

 private:
  static void _isrEsquerdo();
  static void _isrDireito();
};

}  // namespace orion
