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

// ===== CHAVE LIGA/DESLIGA DO SENSOR =====
// O DHT ainda NAO foi instalado fisicamente (situacao em 2026-07-19).
// Enquanto nao estiver no pino, deixe em 0: cada tentativa de leitura num
// pino vazio custa serial perdido (explicacao completa abaixo).
// Quando instalar o sensor, troque para 1 e regrave o firmware.
#define ORION_DHT_INSTALADO 0

constexpr unsigned long DHT_INTERVALO_LEITURA_MS = 5000;

// Quantas leituras invalidas seguidas ate concluir que nao ha sensor no
// pino, e de quanto em quanto tempo tentar de novo depois disso.
//
// Por que isso existe (investigado em 2026-07-19): a biblioteca DHT faz
// bit-banging COM AS INTERRUPCOES DESLIGADAS. Enquanto elas estao
// desligadas a ISR que enche o buffer serial nao roda, e a 115200 baud os
// bytes que chegam se perdem (o hardware do ATmega so segura 2-3). Com o
// sensor AUSENTE e pior ainda: em vez de responder em ~5ms, a leitura
// gasta o timeout inteiro esperando um pino que nunca muda.
//
// Isso corrompia o quadro que estivesse chegando na hora - era a causa
// real das respostas perdidas (~1,5% das requisicoes, todas na mesma fase
// do ciclo de 5s) e provavelmente do ACK perdido de 2026-07-18.
constexpr uint8_t DHT_FALHAS_ATE_DESISTIR = 3;
constexpr unsigned long DHT_INTERVALO_RETENTATIVA_MS = 60000;

class DhtManager {
 public:
  void iniciar() { _dht.begin(); }

  void atualizarSeParado(bool robotParado) {
#if !ORION_DHT_INSTALADO
    (void)robotParado;  // sensor desligado na chave acima - nao toca no pino
    _ausente = true;
    return;
#else
    if (!robotParado) return;

    unsigned long agora = millis();
    // Sem sensor no pino, espaca as tentativas: cada uma custa serial
    // perdido (ver comentario dos limiares acima).
    unsigned long intervalo =
        _ausente ? DHT_INTERVALO_RETENTATIVA_MS : DHT_INTERVALO_LEITURA_MS;
    if (_ultimaLeituraMs != 0 && agora - _ultimaLeituraMs < intervalo) return;
    _ultimaLeituraMs = agora;

    float temperatura = _dht.readTemperature();
    float umidade = _dht.readHumidity();
    _leituraValida = !isnan(temperatura) && !isnan(umidade);

    if (_leituraValida) {
      _temperaturaC = temperatura;
      _umidadePercent = umidade;
      _falhasSeguidas = 0;
      _ausente = false;  // sensor instalado depois: volta a ler normal
      return;
    }

    if (_falhasSeguidas < DHT_FALHAS_ATE_DESISTIR) _falhasSeguidas++;
    if (_falhasSeguidas >= DHT_FALHAS_ATE_DESISTIR) _ausente = true;
#endif
  }

  float temperaturaC() const { return _temperaturaC; }
  float umidadePercent() const { return _umidadePercent; }
  bool leituraValida() const { return _leituraValida; }
  // Verdadeiro quando desistimos: o sensor nao respondeu N vezes seguidas.
  bool ausente() const { return _ausente; }

 private:
  DHT _dht{pinos::DHT_DATA, DHT11};
  unsigned long _ultimaLeituraMs = 0;
  float _temperaturaC = 0;
  float _umidadePercent = 0;
  bool _leituraValida = false;
  uint8_t _falhasSeguidas = 0;
  bool _ausente = false;
};

}  // namespace orion
