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
// Os numeros que ficavam aqui eram do divisor antigo (100k/27k) medido no A0
// do Mega e NAO VALEM MAIS - ver a conta nova, para o ADC de 3,3V do ESP32,
// logo abaixo nas constantes.
//
// ATENCAO - GND COMUM: este divisor exige que o GND da placa e o GND da
// bateria sejam o mesmo. Os motores hoje sao opto-isolados nos TB6600 e
// NAO precisavam disso (ver pins.h), mas a medicao de tensao precisa: sem
// referencia comum a leitura nao significa nada. Como a placa passa a ser
// alimentada pelo buck 5V da propria bateria, o GND ja fica comum.
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
//
// *** VALORES REFEITOS NA MIGRACAO PARA O ESP32 (2026-08-02) ***
// Eram 100k/27k, dimensionados para o ADC de 5V do Mega. Naquela conta o
// pack cheio de 21V entregava 4,46V no pino - folgado sob os 5V do Mega, mas
// **acima dos 3,3V que o ESP32 aguenta**. Se o divisor tivesse sido montado
// antes da troca de placa, ligar a bateria danificaria o GPIO 36.
//
// Nao houve dano porque o divisor NUNCA foi montado (ver a chave abaixo) -
// mas os valores anotados viraram uma armadilha e por isso foram trocados
// aqui, e nao num comentario.
//
// Conta nova, com 100k/15k:
//   fator = 15/(100+15) = 0,1304
//   18V no pack (nominal) -> 2,35V no pino
//   21V no pack (cheio)   -> 2,74V no pino   (folga de 0,56V sob os 3,3V)
//   consumo do divisor: ~0,18mA (nao descarrega o pack)
//
// A folga e de proposito maior que a antiga: o ADC do ESP32 e conhecido por
// ser nao-linear perto do teto da escala, entao trabalhar longe de 3,3V vale
// mais que aproveitar a faixa inteira.
constexpr float BATERIA_R1_OHMS = 100000.0f;
constexpr float BATERIA_R2_OHMS = 15000.0f;
constexpr float BATERIA_FATOR_DIVISOR =
    BATERIA_R2_OHMS / (BATERIA_R1_OHMS + BATERIA_R2_OHMS);

// --- ADC do ESP32: 12 bits, fundo de escala ~3,3V (atenuacao de 11dB, que e
// o padrao do Arduino-ESP32). Era 10 bits e 5V no Mega.
//
// AVISO PARA QUANDO O DIVISOR FOR MONTADO: o ADC do ESP32 nao e linear como
// o do AVR - o erro chega a algumas dezenas de mV e varia de chip para chip.
// Para nivel de bateria (faixas de OK/AVISO/CRITICA separadas por 1,5V) isso
// e irrelevante. Se algum dia alguem quiser tensao PRECISA, calibrar contra
// um multimetro em vez de confiar nesta conta.
constexpr float ADC_TENSAO_REFERENCIA = 3.3f;
constexpr int ADC_PASSOS = 4096;

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
