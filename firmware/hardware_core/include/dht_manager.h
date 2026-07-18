// DHT Manager (Cap 10 secao 8) - temperatura/umidade (pino 24, CONFIRMADO).
//
// A biblioteca DHT bloqueia ~20-25ms por leitura (protocolo bit-banged no
// pino de dados) - por isso so lemos quando o robo esta parado, e no
// maximo a cada DHT_INTERVALO_LEITURA_MS (o proprio sensor nao suporta
// leituras mais frequentes que isso).
//
// Modelo assumido: DHT11 (suposicao do projeto, conforme o guia de
// ligacao). Se o sensor fisico for um DHT22, troque o segundo argumento
// do construtor de `_dht` abaixo - o resto do codigo nao muda.
#pragma once

#include <Arduino.h>
#include <DHT.h>

#include "pins.h"

namespace orion {

constexpr unsigned long DHT_INTERVALO_LEITURA_MS = 5000;

class DhtManager {
 public:
  void iniciar() { _dht.begin(); }

  void atualizarSeParado(bool robotParado) {
    if (!robotParado) return;
    unsigned long agora = millis();
    if (agora - _ultimaLeituraMs < DHT_INTERVALO_LEITURA_MS) return;
    _ultimaLeituraMs = agora;

    float temperatura = _dht.readTemperature();
    float umidade = _dht.readHumidity();
    _leituraValida = !isnan(temperatura) && !isnan(umidade);
    if (_leituraValida) {
      _temperaturaC = temperatura;
      _umidadePercent = umidade;
    }
  }

  float temperaturaC() const { return _temperaturaC; }
  float umidadePercent() const { return _umidadePercent; }
  bool leituraValida() const { return _leituraValida; }

 private:
  DHT _dht{pinos::DHT_DATA, DHT11};
  unsigned long _ultimaLeituraMs = 0;
  float _temperaturaC = 0;
  float _umidadePercent = 0;
  bool _leituraValida = false;
};

}  // namespace orion
