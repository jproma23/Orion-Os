// Safety Manager (Cap 10 secao 9) - seguranca reativa.
//
// Roda independente do Raspberry e do Notebook (Cap 6 regra 7: a camada
// reativa nunca depende dos demais) - mesmo que o link serial caia no meio
// de um movimento, o Mega para sozinho.
#pragma once

#include <Arduino.h>

#include "imu_manager.h"
#include "motor_manager.h"
#include "radar_manager.h"

namespace orion {

constexpr float DISTANCIA_MINIMA_FRENTE_CM = 25.0f;  // default motion.min_front_distance_cm
constexpr unsigned long TIMEOUT_COMANDO_MS = 5000;

class SafetyManager {
 public:
  // Chamar todo loop(). Retorna true no instante em que uma parada de
  // seguranca e ACIONADA (a transicao, nao a cada ciclo em que ela persiste).
  bool avaliar(MotorManager& motores, RadarManager& radar, ImuManager& imu,
               unsigned long ultimoComandoMs) {
    // Cap 18: "em duvida, para". Dois casos em que NAO da pra confiar que
    // a frente esta livre - os dois contam como obstaculo (fail-safe) em
    // vez de "livre pra andar" (fail-open), achados reais da vistoria de
    // 2026-07-24:
    //  1) leitura invalida (sensor desconectado, fio solto, ruido);
    //  2) o servo do radar esta apontado pro lado (SCAN_FRONT em
    //     andamento, ~2,1s) - o MESMO sensor ultrassonico e usado pela
    //     varredura e pela checagem frontal, entao uma leitura valida
    //     "de lado" nao diz nada sobre o que tem na frente de verdade.
    bool obstaculoFrontal = !radar.apontandoParaFrente() ||
                            !radar.distanciaFrontalValida() ||
                            radar.distanciaFrontalCm() < DISTANCIA_MINIMA_FRENTE_CM;
    bool inclinacaoCritica = imu.conectado() && imu.inclinacaoCritica();
    bool impacto = imu.conectado() && imu.impactoDetectado();
    bool timeoutComando =
        motores.emMovimento() && (millis() - ultimoComandoMs) > TIMEOUT_COMANDO_MS;

    bool deveParar = obstaculoFrontal || inclinacaoCritica || impacto || timeoutComando;

    if (deveParar) {
      // Reforca a parada TODO ciclo de loop() enquanto o perigo persistir,
      // nao so na transicao livre->perigo - achado real da vistoria de
      // 2026-07-24: do jeito antigo, se um comando de movimento chegasse
      // do Raspberry com o perigo ainda ativo (retry, fila atrasada, race
      // condition), o motor religava e a seguranca nunca disparava de
      // novo, porque o "if" so rodava na borda. CommandExecutor tambem
      // recusa comando de movimento novo enquanto pararAtivo() for
      // verdadeiro (defesa em profundidade) - mas essa chamada aqui e a
      // que garante que o motor para de fato, mesmo que algo escape
      // daquela outra checagem.
      motores.parar();
      if (!_pararAtivo) {
        _motivo = obstaculoFrontal   ? "obstaculo_frontal"
                  : inclinacaoCritica ? "inclinacao_critica"
                  : impacto           ? "impacto"
                                      : "timeout_comando";
        _pararAtivo = true;
        return true;
      }
      return false;
    }
    _pararAtivo = false;
    return false;
  }

  bool pararAtivo() const { return _pararAtivo; }
  const char* motivo() const { return _motivo; }

 private:
  bool _pararAtivo = false;
  const char* _motivo = "";
};

}  // namespace orion
