#include "encoder_manager.h"

#include <util/atomic.h>

#include "pins.h"

namespace orion {

namespace {
volatile long g_pulsosEsquerdo = 0;
volatile long g_pulsosDireito = 0;

// Sentido comandado de cada roda (true = frente). Volatile porque quem
// escreve e o loop e quem le e a ISR.
volatile bool g_frenteEsquerda = true;
volatile bool g_frenteDireita = true;

// Instante do ultimo pulso aceito, para o corte de repique.
volatile unsigned long g_ultimoPulsoEsquerdoUs = 0;
volatile unsigned long g_ultimoPulsoDireitoUs = 0;
}  // namespace

void EncoderManager::iniciar() {
  pinMode(pinos::ENCODER_ESQUERDO, INPUT_PULLUP);
  pinMode(pinos::ENCODER_DIREITO, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(pinos::ENCODER_ESQUERDO), _isrEsquerdo, RISING);
  attachInterrupt(digitalPinToInterrupt(pinos::ENCODER_DIREITO), _isrDireito, RISING);
}

void EncoderManager::definirSentido(bool frenteEsquerda, bool frenteDireita) {
  g_frenteEsquerda = frenteEsquerda;
  g_frenteDireita = frenteDireita;
}

// LEITURA ATOMICA - nao e preciosismo.
//
// `long` sao 4 bytes e o AVR e de 8 bits: uma leitura comum vira quatro
// acessos separados. Se a ISR do encoder cair no meio, os bytes lidos vem
// metade de antes e metade de depois - o classico "torn read". O estrago
// nao e um erro de um pulso: passando de 0x00FF para 0x0100, uma leitura
// rasgada pode devolver 0x01FF, e o Motion Core recebe um salto de
// centenas de pulsos que nunca aconteceram. Como a odometria trabalha por
// DELTA, esse salto entra na pose e nao sai mais.
//
// ATOMIC_RESTORESTATE em vez de noInterrupts()/interrupts(): se o chamador
// ja estiver com as interrupcoes desligadas, isto NAO as religa por conta
// propria no fim.
long EncoderManager::pulsosEsquerdo() const {
  long valor;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { valor = g_pulsosEsquerdo; }
  return valor;
}

long EncoderManager::pulsosDireito() const {
  long valor;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { valor = g_pulsosDireito; }
  return valor;
}

void EncoderManager::zerar() {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    g_pulsosEsquerdo = 0;
    g_pulsosDireito = 0;
  }
}

// micros() dentro de ISR: o valor e valido, mas o contador de estouro do
// timer0 nao avanca enquanto a ISR roda. Para uma ISR desta duracao (uma
// subtracao e uma soma) isso e irrelevante - o erro maximo seria de um
// estouro, 1024 us, e so se a ISR durasse mais que isso.
void EncoderManager::_isrEsquerdo() {
  unsigned long agoraUs = micros();
  if (agoraUs - g_ultimoPulsoEsquerdoUs < INTERVALO_MINIMO_PULSO_US) return;
  g_ultimoPulsoEsquerdoUs = agoraUs;
  g_pulsosEsquerdo += g_frenteEsquerda ? 1 : -1;
}

void EncoderManager::_isrDireito() {
  unsigned long agoraUs = micros();
  if (agoraUs - g_ultimoPulsoDireitoUs < INTERVALO_MINIMO_PULSO_US) return;
  g_ultimoPulsoDireitoUs = agoraUs;
  g_pulsosDireito += g_frenteDireita ? 1 : -1;
}

}  // namespace orion
