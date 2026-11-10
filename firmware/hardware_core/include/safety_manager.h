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
//
// DUAS REGRAS DESDE 2026-11-10 (auditoria C1-05/C4-03):
//
//   1. O homem-morto de COMANDO (5 s) so e renovado por comando de MOVIMENTO
//      (command_executor.h, renovaHomemMorto). Antes qualquer COMMAND o
//      renovava - inclusive o SET_PAN_TILT que a visao manda varias vezes
//      por segundo seguindo uma pessoa. Com isso o gesto "vem" (MOVE_FORWARD
//      sem STOP depois) andava para sempre: a camera mantinha vivo um
//      movimento que ninguem mais comandava.
//   2. Movimento COM FIM (MOVE_DISTANCE, TURN com graus) nao passa pelo
//      homem-morto de comando - 50 cm levam ~10 s, mais que os 5 s - mas
//      tambem nao anda mais sem dono: ele so continua enquanto o HEARTBEAT do
//      Raspberry (1 por segundo) estiver chegando. Pi travado = o heartbeat
//      cala = parada em TIMEOUT_HEARTBEAT_PI_MS, e nao "anda ate o fim" (2 m
//      eram ~45 s andando sozinho).
#pragma once

#include <Arduino.h>

#include "motor_manager.h"

namespace orion {

constexpr unsigned long TIMEOUT_COMANDO_MS = 5000;
// 2,5 s com heartbeat a 1 Hz (communication.heartbeat_interval_s): aguenta
// UM heartbeat atrasado (o Python manda por timer, com jitter), nunca dois
// perdidos. Abaixo dos 3 s em que o proprio Pi declara o corpo perdido
// (heartbeats_lost_threshold), para o corpo parar antes de o Pi comecar a
// reconectar - a reconexao reseta o ESP32.
constexpr unsigned long TIMEOUT_HEARTBEAT_PI_MS = 2500;
//: Valor de `idadeHeartbeatPiMs` quando o Pi nunca mandou heartbeat.
constexpr unsigned long HEARTBEAT_PI_NUNCA = 0xFFFFFFFFul;

class SafetyManager {
 public:
  // Chamar todo loop(). Retorna true no instante em que uma parada de
  // seguranca e ACIONADA (a transicao, nao a cada ciclo em que ela persiste).
  //
  // `ultimoComandoMs`: millis() do ultimo comando de MOVIMENTO (main.cpp).
  // `idadeHeartbeatPiMs`: ha quanto tempo chegou o ultimo HEARTBEAT do Pi
  // (HEARTBEAT_PI_NUNCA se nunca chegou).
  bool avaliar(MotorManager& motores, unsigned long ultimoComandoMs,
               unsigned long idadeHeartbeatPiMs) {
    const bool comFim = motores.movimentoComFim();
    const bool timeoutComando = motores.emMovimento() && !comFim &&
                                (millis() - ultimoComandoMs) > TIMEOUT_COMANDO_MS;
    const bool semPi = comFim && idadeHeartbeatPiMs > TIMEOUT_HEARTBEAT_PI_MS;

    // Homem-morto: reforcado TODO ciclo enquanto durar, nao so na borda.
    // Um comando atrasado/reenviado nao pode religar o motor de um robo que
    // ficou sem dono (achado real da vistoria de 2026-07-24).
    if (timeoutComando || semPi) {
      motores.parar();
      if (!_pararAtivo) {
        _motivo = semPi ? "heartbeat_pi_perdido" : "timeout_comando";
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
