// Leitura de encoder de quadratura via PCNT (periferico de hardware do
// ESP32, EDR-0031: "nao contar por interrupcao - a alguns kHz isso rouba
// ciclos do laco de controle").
//
// Cada roda usa UMA unidade PCNT com DOIS canais (A e B cruzados), o
// padrao classico de decodificacao 4x: conta as 4 transicoes por ciclo
// completo de quadratura, nao so 1. O contador de hardware e int16_t (pode
// estourar em alta velocidade) - o overflow e tratado por interrupcao de
// limite (H_LIM/L_LIM), rara o bastante para nao pesar no laco de
// controle, e acumulado num contador de 64 bits que nunca estoura na
// pratica.
#pragma once

#include <stdint.h>

namespace orion {

class EncoderPcnt {
 public:
  // unit: unidade PCNT dedicada a esta roda (0 a 7, uma por roda - EDR-0031
  // usa 2 das 8 disponiveis). pino_a/pino_b: os dois canais de quadratura.
  void iniciar(int unidade, int pino_a, int pino_b);

  // Contagem acumulada desde o boot (ou desde zerar()), em ticks do
  // encoder (ja em resolucao 4x). Positivo = sentido "para frente" do fio
  // A, conforme a fiacao real - se uma roda girar ao contrario do
  // esperado, e mais barato inverter A/B na fiacao do que no software
  // (mesma licao do EDR de motores espelhados deste projeto).
  //
  // Soma o overflow ja acumulado (_acumulado) com a leitura ATUAL do
  // contador de hardware - por isso nao e so um getter de campo, precisa
  // consultar o PCNT a cada chamada.
  int64_t ticks() const;

  void zerar();

  // Chamado pela ISR de overflow do PCNT (H_LIM/L_LIM) - nao chamar de
  // outro lugar.
  void _sobre_limite(int16_t direcao);

 private:
  int _unidade = -1;
  volatile int64_t _acumulado = 0;
};

}  // namespace orion
