// Radar Manager (Cap 10 secao 6) - radar frontal (SCAN_FRONT).
//
// CONFIRMADO 2026-07-18: SERVO_RADAR fisicamente montado - a varredura
// abaixo agora move o sensor de verdade, cada angulo le uma distancia
// fisica diferente. distanciaFrontalCm() (sem varredura, angulo atual do
// servo) e o que o Safety Manager usa para obstaculo frontal.
#pragma once

#include <Arduino.h>
#include <Servo.h>

#include "pins.h"
#include "sensor_ultrassonico.h"

namespace orion {

constexpr uint8_t RADAR_QUANTIDADE_ANGULOS = 7;

struct LeituraRadar {
  uint8_t angulo;
  float distanciaCm;
  bool valida;
};

class RadarManager {
 public:
  void iniciar() {
    _ultrassom.iniciar(pinos::ULTRASSOM_FRENTE_TRIG, pinos::ULTRASSOM_FRENTE_ECHO);
    _servo.attach(pinos::SERVO_RADAR);
    _servo.write(90);
  }

  void atualizar() {
    _ultrassom.atualizar();
    if (_varrendo) _atualizarVarredura();
  }

  void iniciarVarredura() {
    _varrendo = true;
    _indiceAtual = 0;
    _comandarServo(_angulos[0]);
  }

  bool varrendo() const { return _varrendo; }
  const LeituraRadar* leituras() const { return _leituras; }
  uint8_t quantidadeLeituras() const { return RADAR_QUANTIDADE_ANGULOS; }

  float distanciaFrontalCm() const { return _ultrassom.distanciaCm(); }
  bool distanciaFrontalValida() const { return _ultrassom.leituraValida(); }

 private:
  // membro de instancia normal (nao static) - evita a exigencia de uma
  // definicao fora da classe que um array "static constexpr" teria no
  // padrao C++ usado por este toolchain AVR.
  uint8_t _angulos[RADAR_QUANTIDADE_ANGULOS] = {0, 30, 60, 90, 120, 150, 180};
  static constexpr unsigned long TEMPO_ASSENTAMENTO_SERVO_MS = 300;

  SensorUltrassonico _ultrassom;
  Servo _servo;
  bool _varrendo = false;
  bool _aguardandoServo = false;
  uint8_t _indiceAtual = 0;
  unsigned long _momentoComandoServo = 0;
  LeituraRadar _leituras[RADAR_QUANTIDADE_ANGULOS];

  void _comandarServo(uint8_t angulo) {
    _servo.write(angulo);
    _momentoComandoServo = millis();
    _aguardandoServo = true;
  }

  void _atualizarVarredura() {
    if (_aguardandoServo) {
      if (millis() - _momentoComandoServo < TEMPO_ASSENTAMENTO_SERVO_MS) return;
      _aguardandoServo = false;
    }

    uint8_t angulo = _angulos[_indiceAtual];
    _leituras[_indiceAtual] = {angulo, _ultrassom.distanciaCm(), _ultrassom.leituraValida()};

    _indiceAtual++;
    if (_indiceAtual >= RADAR_QUANTIDADE_ANGULOS) {
      _varrendo = false;
      _servo.write(90);
      return;
    }
    _comandarServo(_angulos[_indiceAtual]);
  }
};

}  // namespace orion
