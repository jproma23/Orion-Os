// Safety Manager (Cap 10 secao 9) - seguranca reativa.
//
// Roda independente do Raspberry e do Notebook (Cap 6 regra 7: a camada
// reativa nunca depende dos demais) - mesmo que o link serial caia no meio
// de um movimento, o corpo para sozinho.
//
// SOBROU UMA PARADA SO, e ela nao e sobre obstaculo (2026-09-19).
//
// Duas decisoes se somaram ate aqui. A primeira, de 2026-07-29 (EDR-0027):
// *"o mega nao pode vetar, o mega tem informar os dados e procurar uma
// saida"*. Obstaculo deixou de frear e virou DADO na telemetria, com a
// manobra do lado da inteligencia. A segunda, de 2026-09-19: os ultrassons
// sairam do robo e quem mede distancia e o LiDAR, ligado ao Raspberry.
//
// Juntas, elas tiram o obstaculo daqui por completo - nao por escolha de
// projeto, mas porque este processador nao tem mais com o que medir a
// frente. Fingir que tem seria pior: sem sensor, a logica antiga declararia
// `frente_desconhecida` em TODO ciclo, e o Pi receberia um alarme permanente
// que nao significa nada.
//
// O homem-morto fica, e nao contradiz o EDR-0027: se os motores estao
// girando e nenhum comando chega ha TIMEOUT_COMANDO_MS, nao existe manobra a
// preservar, porque nao existe ninguem manobrando - o link caiu, o Pi
// travou, o processo morreu. Parar ai nao vence uma decisao da inteligencia:
// e o que sobra na ausencia dela. Sem isto, um cabo solto deixa o robo
// andando sozinho ate bater em alguma coisa.
#pragma once

#include <Arduino.h>

#include "motor_manager.h"

namespace orion {

constexpr unsigned long TIMEOUT_COMANDO_MS = 5000;

class SafetyManager {
 public:
  // Chamar todo loop(). Retorna true no instante em que uma parada de
  // seguranca e ACIONADA (a transicao, nao a cada ciclo em que ela persiste).
  bool avaliar(MotorManager& motores, unsigned long ultimoComandoMs) {
    const bool timeoutComando =
        motores.emMovimento() && (millis() - ultimoComandoMs) > TIMEOUT_COMANDO_MS;

    // Homem-morto: reforcado TODO ciclo enquanto durar, nao so na borda.
    // Um comando atrasado/reenviado nao pode religar o motor de um robo que
    // ficou sem dono (achado real da vistoria de 2026-07-24).
    if (timeoutComando) {
      motores.parar();
      if (!_pararAtivo) {
        _motivo = "timeout_comando";
        _pararAtivo = true;
        return true;
      }
      return false;
    }
    _pararAtivo = false;
    return false;
  }

  //: "ha perigo detectado agora". NAO significa "movimento recusado"
  //: (EDR-0027) - e informacao para o Pi decidir a manobra.
  bool pararAtivo() const { return _pararAtivo; }
  const char* motivo() const { return _motivo; }

 private:
  bool _pararAtivo = false;
  const char* _motivo = "";
};

}  // namespace orion
