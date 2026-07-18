// Driver HC-SR04 nao bloqueante (Cap 10 secao 10).
//
// pulseIn() e o jeito "facil" de ler um HC-SR04, mas bloqueia ate 1s por
// leitura por padrao - inaceitavel no loop principal (Cap 10 secao 10:
// "nunca bloquear"). Esta classe e uma maquina de estados: cada chamada de
// atualizar() avanca um passo, sem nunca esperar.
#pragma once

#include <Arduino.h>

namespace orion {

class SensorUltrassonico {
 public:
  void iniciar(uint8_t pinoTrig, uint8_t pinoEcho) {
    _pinoTrig = pinoTrig;
    _pinoEcho = pinoEcho;
    pinMode(_pinoTrig, OUTPUT);
    pinMode(_pinoEcho, INPUT);
    digitalWrite(_pinoTrig, LOW);
  }

  void atualizar() {
    unsigned long agora = micros();
    switch (_estagio) {
      case Estagio::OCIOSO:
        if (agora - _ultimaLeituraUs >= INTERVALO_ENTRE_LEITURAS_US) {
          digitalWrite(_pinoTrig, HIGH);
          delayMicroseconds(10);  // pulso minimo de trigger do HC-SR04
          digitalWrite(_pinoTrig, LOW);
          _inicioEsperaUs = agora;
          _estagio = Estagio::AGUARDANDO_SUBIDA;
        }
        break;

      case Estagio::AGUARDANDO_SUBIDA:
        if (digitalRead(_pinoEcho) == HIGH) {
          _inicioEchoUs = agora;
          _estagio = Estagio::AGUARDANDO_DESCIDA;
        } else if (agora - _inicioEsperaUs > TIMEOUT_US) {
          _semLeitura(agora);
        }
        break;

      case Estagio::AGUARDANDO_DESCIDA:
        if (digitalRead(_pinoEcho) == LOW) {
          unsigned long duracao = agora - _inicioEchoUs;
          _distanciaCm = duracao / 58.0f;  // formula padrao do HC-SR04
          _leituraValida = true;
          _ultimaLeituraUs = agora;
          _estagio = Estagio::OCIOSO;
        } else if (agora - _inicioEchoUs > TIMEOUT_US) {
          _semLeitura(agora);
        }
        break;
    }
  }

  float distanciaCm() const { return _distanciaCm; }
  bool leituraValida() const { return _leituraValida; }

 private:
  enum class Estagio { OCIOSO, AGUARDANDO_SUBIDA, AGUARDANDO_DESCIDA };
  static constexpr unsigned long INTERVALO_ENTRE_LEITURAS_US = 60000;  // 60ms (datasheet)
  static constexpr unsigned long TIMEOUT_US = 30000;  // ~5m de alcance maximo

  uint8_t _pinoTrig = 0;
  uint8_t _pinoEcho = 0;
  Estagio _estagio = Estagio::OCIOSO;
  unsigned long _ultimaLeituraUs = 0;
  unsigned long _inicioEsperaUs = 0;
  unsigned long _inicioEchoUs = 0;
  float _distanciaCm = -1;
  bool _leituraValida = false;

  void _semLeitura(unsigned long agora) {
    _leituraValida = false;
    _ultimaLeituraUs = agora;
    _estagio = Estagio::OCIOSO;
  }
};

}  // namespace orion
