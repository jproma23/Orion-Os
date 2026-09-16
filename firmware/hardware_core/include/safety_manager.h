// Safety Manager (Cap 10 secao 9) - seguranca reativa.
//
// Roda independente do Raspberry e do Notebook (Cap 6 regra 7: a camada
// reativa nunca depende dos demais) - mesmo que o link serial caia no meio
// de um movimento, o Mega para sozinho.
#pragma once

#include <Arduino.h>

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
  bool avaliar(MotorManager& motores, RadarManager& radar,
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
    bool timeoutComando =
        motores.emMovimento() && (millis() - ultimoComandoMs) > TIMEOUT_COMANDO_MS;

    // O MEGA INFORMA, NAO VETA (2026-07-29, EDR-0027).
    //
    // Decisao do mantenedor, dita e reafirmada: *"o mega nao pode vetar, o
    // mega tem informar os dados e procurar uma saida"*, *"a inteligencia
    // recebe todos os dados e dai decide o q fazer"*, *"nada tem q bloquear
    // ou impedir de tomar acoes e atitudes"*.
    //
    // O que isso muda: um perigo detectado NAO recusa mais comando nenhum
    // (ver command_executor.h, onde a recusa foi removida). Perigo continua
    // sendo DETECTADO e RELATADO com o mesmo rigor de antes - `_motivo` e os
    // campos de telemetria sao a materia-prima com que o Pi decide.
    //
    // NAO TEM FREIO, EXISTEM MANOBRAS - regra do mantenedor, 2026-07-29.
    //
    // Obstaculo a frente NAO para mais o robo por conta do firmware. Vira
    // DADO na telemetria, e tem uma manobra correspondente do lado da
    // inteligencia (contornar) - ver EDR-0027. O Mega mede, informa e
    // executa.
    //
    // Sobra UMA parada, e ela nao contradiz a regra: o homem-morto. Se os
    // motores estao girando e nenhum comando chega ha TIMEOUT_COMANDO_MS, nao
    // existe manobra a preservar, porque nao existe ninguem manobrando - o
    // link caiu, o Pi travou, o processo morreu. Parar ai nao vence uma
    // decisao da inteligencia: e o que sobra na ausencia dela. Sem isto, um
    // cabo solto deixa o robo andando sozinho ate bater em alguma coisa.
    const bool perigoFrontal = obstaculoFrontal || frenteDesconhecida;

    // `_pararAtivo` continua significando "ha perigo detectado agora" - e o
    // que alimenta `seguranca_parada_ativa`/`seguranca_motivo` e o estado
    // OBSTACLE_DETECTED, que e como o Pi fica sabendo que precisa manobrar.
    // Ele deixou de implicar recusa de comando e deixou de implicar freio.
    bool deveParar = timeoutComando || perigoFrontal;

    // A UNICA parada automatica que restou.
    const bool deveFrear = timeoutComando;

    // Homem-morto: reforcado TODO ciclo enquanto durar, nao so na borda.
    // Um comando atrasado/reenviado nao pode religar o motor de um robo que
    // ficou sem dono (achado real da vistoria de 2026-07-24).
    if (deveFrear) {
      motores.parar();
    }

    if (deveParar) {
      if (!_pararAtivo) {
        // Motivos distintos de proposito: ate 28/07 "radar olhando pro
        // lado" era reportado como "obstaculo_frontal", e o diagnostico
        // mentia - dizia que havia um obstaculo que os proprios sensores
        // desmentiam. Quem le a telemetria agora sabe qual dos dois e.
        _motivo = obstaculoFrontal    ? "obstaculo_frontal"
                  : frenteDesconhecida ? "frente_desconhecida"
                                       : "timeout_comando";
        _pararAtivo = true;
        return true;
      }
      return false;
    }
    _pararAtivo = false;
    return false;
  }

  //: "ha perigo detectado agora". NAO significa mais "movimento recusado" nem
  //: "motor freado" (EDR-0027) - e informacao para o Pi decidir a manobra.
  bool pararAtivo() const { return _pararAtivo; }
  const char* motivo() const { return _motivo; }

 private:
  bool _pararAtivo = false;
  const char* _motivo = "";
};

}  // namespace orion
