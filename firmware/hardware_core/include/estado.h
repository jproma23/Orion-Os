// Maquina de estados do Hardware Core (Cap 10 secao 4).
//
// BOOT -> READY -> IDLE -> EXECUTING_MISSION -> ... -> SHUTDOWN.
// Toda transicao e reportada ao Motion Core (via telemetria/evento) e
// registrada - quem consome isso e o main.cpp, esta classe so guarda o
// estado atual e sinaliza quando ele muda.
#pragma once

#include <Arduino.h>

namespace orion {

enum class Estado : uint8_t {
  BOOT,
  READY,
  IDLE,
  EXECUTING_MISSION,
  OBSTACLE_DETECTED,
  MISSION_PAUSED,
  ERROR,
  SAFE_MODE,
  SHUTDOWN,
};

inline const char* nomeEstado(Estado estado) {
  switch (estado) {
    case Estado::BOOT:
      return "BOOT";
    case Estado::READY:
      return "READY";
    case Estado::IDLE:
      return "IDLE";
    case Estado::EXECUTING_MISSION:
      return "EXECUTING_MISSION";
    case Estado::OBSTACLE_DETECTED:
      return "OBSTACLE_DETECTED";
    case Estado::MISSION_PAUSED:
      return "MISSION_PAUSED";
    case Estado::ERROR:
      return "ERROR";
    case Estado::SAFE_MODE:
      return "SAFE_MODE";
    case Estado::SHUTDOWN:
      return "SHUTDOWN";
  }
  return "DESCONHECIDO";
}

class MaquinaDeEstados {
 public:
  Estado atual() const { return _atual; }

  // Retorna true se o estado realmente mudou (para quem chama decidir
  // notificar o Motion Core so quando ha uma transicao de verdade).
  bool transicionarPara(Estado novoEstado) {
    if (novoEstado == _atual) return false;
    _anterior = _atual;
    _atual = novoEstado;
    return true;
  }

  Estado anterior() const { return _anterior; }

 private:
  Estado _atual = Estado::BOOT;
  Estado _anterior = Estado::BOOT;
};

}  // namespace orion
