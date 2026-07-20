// Bateria Manager (Cap 16) - tensao do pack por divisor resistivo em A0.
//
// A bateria e um pack de parafusadeira de 18V (5 celulas Li-ion). Packs
// de ferramenta NAO tem medidor de carga acessivel - a protecao interna
// so corta, e corta de repente. Por isso medimos a tensao nos: da para
// avisar e recolher o robo antes do corte seco, em vez de ele morrer no
// meio do caminho.
//
// LIGACAO (divisor de tensao):
//
//   BAT+ (18V) ---[ R1 = 100k ]---+---[ R2 = 27k ]--- GND
//                                 |
//                                 +--- A0 do Mega
//
//   18V no pack -> 3,83V em A0 (folga sob os 5V da entrada)
//   21V no pack -> 4,46V em A0 (ainda seguro, se o pack vier mais cheio)
//   consumo do divisor: ~0,14mA (nao descarrega o pack)
//
// ATENCAO - GND COMUM: este divisor exige que o GND do Mega e o GND da
// bateria sejam o mesmo. Os motores hoje sao opto-isolados nos TB6600 e
// NAO precisavam disso (ver pins.h), mas a medicao de tensao precisa: sem
// referencia comum a leitura nao significa nada. Como o Mega passa a ser
// alimentado pelo buck 5V da propria bateria, o GND ja fica comum.
#pragma once

#include <Arduino.h>

namespace orion {

// ===== CHAVE LIGA/DESLIGA =====
// O divisor resistivo ainda NAO foi montado (situacao em 2026-07-19).
// Enquanto nao estiver, deixe em 0: pino analogico solto FLUTUA e
// devolveria uma tensao inventada que a interface mostraria como se fosse
// leitura de verdade - pior que nao ter dado nenhum.
// Depois de montar o divisor, troque para 1 e regrave o firmware.
#define ORION_BATERIA_INSTALADA 0

// --- Divisor resistivo (ajuste aqui se trocar os resistores) ---
constexpr float BATERIA_R1_OHMS = 100000.0f;
constexpr float BATERIA_R2_OHMS = 27000.0f;
constexpr float BATERIA_FATOR_DIVISOR =
    BATERIA_R2_OHMS / (BATERIA_R1_OHMS + BATERIA_R2_OHMS);

// --- ADC do Mega: 10 bits, referencia de 5V ---
constexpr float ADC_TENSAO_REFERENCIA = 5.0f;
constexpr int ADC_PASSOS = 1024;

// --- Limiares do pack 5S Li-ion (por celula: 3,6V nominal / 3,0V vazio) ---
constexpr float BATERIA_TENSAO_CHEIA_V = 18.0f;    // maximo informado pelo usuario
constexpr float BATERIA_TENSAO_AVISO_V = 16.5f;    // 3,3V/celula - hora de recolher
constexpr float BATERIA_TENSAO_CRITICA_V = 15.0f;  // 3,0V/celula - parar ja
constexpr float BATERIA_TENSAO_VAZIA_V = 15.0f;    // referencia dos 0% estimados

// Quantas amostras por leitura. O ADC balanca alguns milivolts e os
// motores injetam ruido no barramento; a media suaviza sem custar tempo
// perceptivel (analogRead leva ~100us, entao 8 amostras = ~0,8ms).
constexpr uint8_t BATERIA_AMOSTRAS = 8;
constexpr unsigned long BATERIA_INTERVALO_LEITURA_MS = 1000;

enum class NivelBateria : uint8_t { DESCONHECIDO, OK, AVISO, CRITICA };

class BateriaManager {
 public:
  void iniciar(uint8_t pino) {
    _pino = pino;
    pinMode(_pino, INPUT);
  }

  void atualizar() {
#if !ORION_BATERIA_INSTALADA
    return;  // divisor nao montado - _lida fica false e nada e reportado
#else
    unsigned long agora = millis();
    if (_ultimaLeituraMs != 0 &&
        agora - _ultimaLeituraMs < BATERIA_INTERVALO_LEITURA_MS) {
      return;
    }
    _ultimaLeituraMs = agora;

    // analogRead nao bloqueia interrupcoes (diferente do DHT - ver
    // dht_manager.h), entao pode rodar no loop sem risco para a serial.
    uint32_t soma = 0;
    for (uint8_t i = 0; i < BATERIA_AMOSTRAS; i++) {
      soma += analogRead(_pino);
    }
    float leituraMedia = static_cast<float>(soma) / BATERIA_AMOSTRAS;

    float tensaoNoPino = leituraMedia * ADC_TENSAO_REFERENCIA / ADC_PASSOS;
    _tensaoV = tensaoNoPino / BATERIA_FATOR_DIVISOR;
    _lida = true;
#endif
  }

  float tensaoV() const { return _tensaoV; }
  bool lida() const { return _lida; }

  // Porcentagem ESTIMADA e so isso: a curva de descarga do Li-ion nao e
  // reta, entao no meio da faixa o numero erra bastante. Serve para a
  // interface mostrar algo; decisao de seguranca deve usar o nivel.
  uint8_t percentualEstimado() const {
    if (!_lida) return 0;
    float faixa = BATERIA_TENSAO_CHEIA_V - BATERIA_TENSAO_VAZIA_V;
    float posicao = (_tensaoV - BATERIA_TENSAO_VAZIA_V) / faixa;
    return static_cast<uint8_t>(constrain(posicao, 0.0f, 1.0f) * 100.0f);
  }

  NivelBateria nivel() const {
    if (!_lida) return NivelBateria::DESCONHECIDO;
    if (_tensaoV <= BATERIA_TENSAO_CRITICA_V) return NivelBateria::CRITICA;
    if (_tensaoV <= BATERIA_TENSAO_AVISO_V) return NivelBateria::AVISO;
    return NivelBateria::OK;
  }

  static const char* nomeNivel(NivelBateria n) {
    switch (n) {
      case NivelBateria::OK: return "ok";
      case NivelBateria::AVISO: return "aviso";
      case NivelBateria::CRITICA: return "critica";
      default: return "desconhecido";
    }
  }

 private:
  uint8_t _pino = A0;
  unsigned long _ultimaLeituraMs = 0;
  float _tensaoV = 0;
  bool _lida = false;
};

}  // namespace orion
