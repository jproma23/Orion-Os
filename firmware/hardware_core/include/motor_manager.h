// Motor Manager (Cap 10 secao 10) - controle dos 2x NEMA17 via 2x TB6600.
//
// Gera pulsos STEP por temporizacao com micros(), sem delay() no loop
// principal (Cap 10 secao 10: "nunca bloquear"). Toda a logica de
// velocidade/aceleracao fica encapsulada aqui - o Raspberry so manda
// comandos de alto nivel (MOVE_FORWARD, MOVE_DISTANCE, ...).
//
// Conversao distancia<->passos e grau<->passos usa constantes de bancada
// (PASSOS_POR_METRO, PASSOS_POR_GRAU) - sem calibracao fisica real ainda
// (isso e a autocalibracao da Fase 12/Cap 12 s.9). Ate la, MOVE_DISTANCE e
// TURN_*  com angulo sao aproximados; TURN_* sem angulo (girar ate STOP) e
// preciso porque nao depende dessas constantes.
#pragma once

#include <Arduino.h>

#include "pins.h"

namespace orion {

constexpr float PASSOS_POR_METRO = 4000.0f;  // igual ao default de config/orion.yaml
constexpr float PASSOS_POR_GRAU = 20.0f;     // aproximado - ajustar na autocalibracao
constexpr long PASSOS_POR_SEGUNDO_MAXIMO = 2000;
constexpr long PASSOS_POR_SEGUNDO_MINIMO = 50;
// Testado na bancada em 2026-07-18: os TB6600 desta montagem so habilitam
// com o pino em HIGH (nao no padrao "ativo em baixo" mais comum) - motor
// ficava completamente mudo/parado (sem nem vibrar) com o valor antigo
// (true), sinal classico de driver permanentemente desabilitado.
constexpr bool ENABLE_ATIVO_EM_BAIXO = false;

struct EstadoRoda {
  uint8_t pinoStep;
  uint8_t pinoDir;
  bool sentidoFrente = true;
  long intervaloUs = 0;      // 0 = parada
  unsigned long ultimoPassoUs = 0;
  long passosRestantes = 0;  // -1 = continuo (ate parar() ser chamado)
  long passosAcumulados = 0; // odometria simples (sem encoder)
};

class MotorManager {
 public:
  void iniciar() {
    pinMode(pinos::STEP_ESQUERDO, OUTPUT);
    pinMode(pinos::DIR_ESQUERDO, OUTPUT);
    pinMode(pinos::STEP_DIREITO, OUTPUT);
    pinMode(pinos::DIR_DIREITO, OUTPUT);
    pinMode(pinos::ENABLE_MOTORES, OUTPUT);

    _esquerda.pinoStep = pinos::STEP_ESQUERDO;
    _esquerda.pinoDir = pinos::DIR_ESQUERDO;
    _direita.pinoStep = pinos::STEP_DIREITO;
    _direita.pinoDir = pinos::DIR_DIREITO;

    habilitar();
  }

  void habilitar() {
    digitalWrite(pinos::ENABLE_MOTORES, ENABLE_ATIVO_EM_BAIXO ? LOW : HIGH);
  }

  void desabilitar() {
    digitalWrite(pinos::ENABLE_MOTORES, ENABLE_ATIVO_EM_BAIXO ? HIGH : LOW);
  }

  // Chamar em todo loop() - gera os pulsos STEP conforme o tempo decorrido.
  void atualizar() {
    unsigned long agora = micros();
    _atualizarRoda(_esquerda, agora);
    _atualizarRoda(_direita, agora);
  }

  void andarFrente(float velocidadePercent) {
    _configurarRoda(_esquerda, true, velocidadePercent, -1);
    _configurarRoda(_direita, true, velocidadePercent, -1);
  }

  void andarDistancia(float distanciaCm, float velocidadePercent) {
    long passos = static_cast<long>(fabs(distanciaCm) * (PASSOS_POR_METRO / 100.0f));
    bool frente = distanciaCm >= 0;
    _configurarRoda(_esquerda, frente, velocidadePercent, passos);
    _configurarRoda(_direita, frente, velocidadePercent, passos);
  }

  void girarContinuo(bool sentidoHorario, float velocidadePercent) {
    _configurarRoda(_esquerda, sentidoHorario, velocidadePercent, -1);
    _configurarRoda(_direita, !sentidoHorario, velocidadePercent, -1);
  }

  void girarGraus(float graus, bool sentidoHorario, float velocidadePercent) {
    long passos = static_cast<long>(fabs(graus) * PASSOS_POR_GRAU);
    _configurarRoda(_esquerda, sentidoHorario, velocidadePercent, passos);
    _configurarRoda(_direita, !sentidoHorario, velocidadePercent, passos);
  }

  void parar() {
    _esquerda.intervaloUs = 0;
    _esquerda.passosRestantes = 0;
    _direita.intervaloUs = 0;
    _direita.passosRestantes = 0;
  }

  bool emMovimento() const {
    return _esquerda.passosRestantes != 0 || _direita.passosRestantes != 0;
  }

  long passosAcumuladosEsquerda() const { return _esquerda.passosAcumulados; }
  long passosAcumuladosDireita() const { return _direita.passosAcumulados; }

 private:
  EstadoRoda _esquerda;
  EstadoRoda _direita;

  static long _intervaloParaVelocidade(float velocidadePercent) {
    velocidadePercent = constrain(velocidadePercent, 1.0f, 100.0f);
    long passosPorSegundo = static_cast<long>(
        PASSOS_POR_SEGUNDO_MINIMO +
        (PASSOS_POR_SEGUNDO_MAXIMO - PASSOS_POR_SEGUNDO_MINIMO) * (velocidadePercent / 100.0f));
    return 1000000L / passosPorSegundo;
  }

  static void _configurarRoda(EstadoRoda& roda, bool sentidoFrente, float velocidadePercent,
                               long passos) {
    roda.sentidoFrente = sentidoFrente;
    digitalWrite(roda.pinoDir, sentidoFrente ? HIGH : LOW);
    roda.intervaloUs = _intervaloParaVelocidade(velocidadePercent);
    roda.passosRestantes = passos;
    roda.ultimoPassoUs = micros();
  }

  static void _atualizarRoda(EstadoRoda& roda, unsigned long agora) {
    if (roda.passosRestantes == 0) return;
    if (agora - roda.ultimoPassoUs < static_cast<unsigned long>(roda.intervaloUs)) return;

    digitalWrite(roda.pinoStep, HIGH);
    delayMicroseconds(3);  // largura minima do pulso STEP do TB6600
    digitalWrite(roda.pinoStep, LOW);

    roda.ultimoPassoUs = agora;
    roda.passosAcumulados += roda.sentidoFrente ? 1 : -1;
    if (roda.passosRestantes > 0) {
      roda.passosRestantes--;
    }
  }
};

}  // namespace orion
