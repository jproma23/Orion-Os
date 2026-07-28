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

// Por quanto tempo uma leitura frontal boa continua valendo depois que o
// radar desviou. Uma varredura completa leva ~2,1s (7 angulos x 300ms de
// assentamento), entao 2500ms cobre uma varredura inteira com folga.
constexpr unsigned long VALIDADE_FRONTAL_MS = 2500;

// Folga EXTRA exigida para andar com a leitura de memoria em vez da leitura
// ao vivo. Andar apoiado numa medida de ate 2,5s atras so e aceitavel se
// aquela medida mostrava MUITO espaco: 25cm de limiar + 75cm de margem =
// so libera com mais de 1 metro livre. Assim a memoria nunca substitui o
// sensor perto de um obstaculo - so cobre o caso de sala aberta, que e
// exatamente onde a varredura travava o robo.
constexpr float MARGEM_FRONTAL_CEGO_CM = 75.0f;

class SafetyManager {
 public:
  // Chamar todo loop(). Retorna true no instante em que uma parada de
  // seguranca e ACIONADA (a transicao, nao a cada ciclo em que ela persiste).
  bool avaliar(MotorManager& motores, RadarManager& radar, ImuManager& imu,
               unsigned long ultimoComandoMs) {
    // Cap 18: "em duvida, para" - continua valendo. O que mudou mais tarde em 28/07 e
    // O QUE CONTA COMO DUVIDA. Leitura invalida (sensor solto, fio, ruido)
    // e duvida e para o robo, como desde a vistoria de 2026-07-24. Ja o
    // servo do radar apontado pro lado durante um SCAN_FRONT (~2,1s) NAO e
    // duvida quando a frente acabou de ser medida e estava vazia - ver o
    // bloco abaixo.
    // CORRIGIDO 2026-07-28 (bug de prioridade maxima do journal de 28/07).
    // A regra antiga era `!radar.apontandoParaFrente() || ...`: enquanto o
    // radar varria, a seguranca declarava OBSTACULO FRONTAL. Medido ao vivo:
    // o radar passa 47% do tempo fora dos 90 graus e o firmware passava
    // EXATAMENTE os mesmos 47% em OBSTACLE_DETECTED, com a frente marcando
    // 199cm validos o tempo todo. Pior, isso se retroalimentava: o
    // comportamento `vigilancia_obstaculo` (prioridade 100) tomava o
    // controle 46 vezes em 100s, e o robo nunca conseguia andar. NAO era o
    // latch do a82d112 - nao ha latch nenhum aqui, _pararAtivo reseta certo.
    //
    // A correcao nao afrouxa a seguranca, separa dois casos que eram um so:
    //  - olhando pra frente: decide pela leitura ao vivo, como sempre;
    //  - olhando pro lado: vale a ultima leitura frontal de verdade, se for
    //    recente E tiver mostrado mais de 1 metro livre (margem acima);
    //  - olhando pro lado sem memoria recente: PARA, como antes (Cap 18,
    //    "em duvida, para") - mas agora reportando o motivo certo.
    const bool cegoParaFrente = !radar.apontandoParaFrente();
    bool frenteDesconhecida = false;
    bool obstaculoFrontal = false;
    if (!cegoParaFrente) {
      obstaculoFrontal = !radar.distanciaFrontalValida() ||
                         radar.distanciaFrontalCm() < DISTANCIA_MINIMA_FRENTE_CM;
    } else if (radar.temFrontalRecente(VALIDADE_FRONTAL_MS)) {
      obstaculoFrontal = radar.frontalRecenteCm() <
                         (DISTANCIA_MINIMA_FRENTE_CM + MARGEM_FRONTAL_CEGO_CM);
    } else {
      frenteDesconhecida = true;
    }
    bool inclinacaoCritica = imu.conectado() && imu.inclinacaoCritica();
    bool impacto = imu.conectado() && imu.impactoDetectado();
    bool timeoutComando =
        motores.emMovimento() && (millis() - ultimoComandoMs) > TIMEOUT_COMANDO_MS;

    bool deveParar = obstaculoFrontal || frenteDesconhecida || inclinacaoCritica ||
                     impacto || timeoutComando;

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
        // Motivos distintos de proposito: ate 28/07 "radar olhando pro
        // lado" era reportado como "obstaculo_frontal", e o diagnostico
        // mentia - dizia que havia um obstaculo que os proprios sensores
        // desmentiam. Quem le a telemetria agora sabe qual dos dois e.
        _motivo = obstaculoFrontal    ? "obstaculo_frontal"
                  : frenteDesconhecida ? "frente_desconhecida"
                  : inclinacaoCritica  ? "inclinacao_critica"
                  : impacto            ? "impacto"
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
