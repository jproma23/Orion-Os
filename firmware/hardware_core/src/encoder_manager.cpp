#include "encoder_manager.h"

#include "pins.h"

namespace orion {

namespace {
volatile long g_pulsosEsquerdo = 0;
volatile long g_pulsosDireito = 0;
}  // namespace

void EncoderManager::iniciar() {
  // ATENCAO AO PULL-UP (2026-08-02, migracao para o ESP32).
  //
  // ENCODER_ESQUERDO ficou no GPIO 39, que e um dos pinos "so leitura"
  // (34-39) e NAO TEM resistor de pull-up interno. Este INPUT_PULLUP nao
  // falha nem avisa - ele simplesmente nao tem efeito nesse pino. Se o
  // encoder que for instalado for de coletor aberto (a maioria dos opticos
  // de roda e), esta entrada vai FLUTUAR e contar pulsos que nao existem,
  // que e a pior falha possivel para odometria: numero que parece medida.
  //
  // ENCODER_DIREITO ficou no GPIO 16 justamente por isso - la o pull-up
  // interno funciona de verdade. Na hora de instalar os encoders: ou se
  // poe um resistor externo de 10k no 39, ou se move o esquerdo para outro
  // GPIO comum (12 e 15 estao livres, mas leia o aviso sobre eles em
  // pins.h antes).
  pinMode(pinos::ENCODER_ESQUERDO, INPUT_PULLUP);
  pinMode(pinos::ENCODER_DIREITO, INPUT_PULLUP);
  // No Mega os encoders eram obrigados a viver nos pinos 18/19, os unicos
  // com interrupcao externa livre. No ESP32 qualquer GPIO interrompe.
  attachInterrupt(digitalPinToInterrupt(pinos::ENCODER_ESQUERDO), _isrEsquerdo, RISING);
  attachInterrupt(digitalPinToInterrupt(pinos::ENCODER_DIREITO), _isrDireito, RISING);
}

long EncoderManager::pulsosEsquerdo() const { return g_pulsosEsquerdo; }
long EncoderManager::pulsosDireito() const { return g_pulsosDireito; }

// IRAM_ATTR e OBRIGATORIO no ESP32 e nao tem equivalente no AVR.
//
// Por padrao o codigo mora na flash e e lido por cache. Uma interrupcao pode
// cair exatamente enquanto a flash esta ocupada (uma escrita de EEPROM, por
// exemplo - e este firmware grava calibracao em EEPROM). Se a rotina de
// interrupcao estiver na flash nesse instante, o chip nao consegue busca-la
// e entra em panico com "Cache disabled but cached memory region accessed",
// reiniciando o robo. IRAM_ATTR forca estas duas funcoes para a RAM interna,
// onde estao sempre acessiveis.
void IRAM_ATTR EncoderManager::_isrEsquerdo() { g_pulsosEsquerdo++; }
void IRAM_ATTR EncoderManager::_isrDireito() { g_pulsosDireito++; }

}  // namespace orion
