#include "encoder_manager.h"

#include "pins.h"

namespace orion {

namespace {
volatile long g_pulsosEsquerdo = 0;
volatile long g_pulsosDireito = 0;
}  // namespace

void EncoderManager::iniciar() {
  pinMode(pinos::ENCODER_ESQUERDO, INPUT_PULLUP);
  pinMode(pinos::ENCODER_DIREITO, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(pinos::ENCODER_ESQUERDO), _isrEsquerdo, RISING);
  attachInterrupt(digitalPinToInterrupt(pinos::ENCODER_DIREITO), _isrDireito, RISING);
}

long EncoderManager::pulsosEsquerdo() const { return g_pulsosEsquerdo; }
long EncoderManager::pulsosDireito() const { return g_pulsosDireito; }

void EncoderManager::_isrEsquerdo() { g_pulsosEsquerdo++; }
void EncoderManager::_isrDireito() { g_pulsosDireito++; }

}  // namespace orion
