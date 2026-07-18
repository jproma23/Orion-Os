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
    bool obstaculoFrontal = radar.distanciaFrontalValida() &&
                            radar.distanciaFrontalCm() < DISTANCIA_MINIMA_FRENTE_CM;
    bool inclinacaoCritica = imu.conectado() && imu.inclinacaoCritica();
    bool impacto = imu.conectado() && imu.impactoDetectado();
    bool timeoutComando =
        motores.emMovimento() && (millis() - ultimoComandoMs) > TIMEOUT_COMANDO_MS;

    bool deveParar = obstaculoFrontal || inclinacaoCritica || impacto || timeoutComando;

    if (deveParar && !_pararAtivo) {
      motores.parar();
      _motivo = obstaculoFrontal   ? "obstaculo_frontal"
                : inclinacaoCritica ? "inclinacao_critica"
                : impacto           ? "impacto"
                                    : "timeout_comando";
      _pararAtivo = true;
      return true;
    }
    if (!deveParar) {
      _pararAtivo = false;
    }
    return false;
  }

  bool pararAtivo() const { return _pararAtivo; }
  const char* motivo() const { return _motivo; }

 private:
  bool _pararAtivo = false;
  const char* _motivo = "";
};

}  // namespace orion
