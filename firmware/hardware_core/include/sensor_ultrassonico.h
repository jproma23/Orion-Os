// Driver HC-SR04 nao bloqueante (Cap 10 secao 10).
//
// pulseIn() e o jeito "facil" de ler um HC-SR04, mas bloqueia ate 1s por
// leitura por padrao - inaceitavel no loop principal (Cap 10 secao 10:
// "nunca bloquear"). Esta classe e uma maquina de estados: cada chamada de
// atualizar() avanca um passo, sem nunca esperar.
#pragma once

#include <Arduino.h>

namespace orion {

class SensorUltrassonico {
 public:
  void iniciar(uint8_t pinoTrig, uint8_t pinoEcho) {
    _pinoTrig = pinoTrig;
    _pinoEcho = pinoEcho;
    pinMode(_pinoTrig, OUTPUT);
    pinMode(_pinoEcho, INPUT);
    digitalWrite(_pinoTrig, LOW);
  }

  void atualizar() {
    unsigned long agora = micros();
    switch (_estagio) {
      case Estagio::OCIOSO:
        if (agora - _ultimaLeituraUs >= INTERVALO_ENTRE_LEITURAS_US) {
          digitalWrite(_pinoTrig, HIGH);
          delayMicroseconds(10);  // pulso minimo de trigger do HC-SR04
          digitalWrite(_pinoTrig, LOW);
          _inicioEsperaUs = agora;
          _estagio = Estagio::AGUARDANDO_SUBIDA;
        }
        break;

      case Estagio::AGUARDANDO_SUBIDA:
        if (digitalRead(_pinoEcho) == HIGH) {
          _inicioEchoUs = agora;
          _estagio = Estagio::AGUARDANDO_DESCIDA;
        } else if (agora - _inicioEsperaUs > TIMEOUT_US) {
          // O ECHO nunca subiu: o sensor nao deu sinal de vida. Isso SIM e
          // defeito (fio solto, modulo queimado, sem 5V) - leitura invalida,
          // e o SafetyManager para o robo (Cap 18: em duvida, para).
          _semResposta(agora);
        }
        break;

      case Estagio::AGUARDANDO_DESCIDA:
        if (digitalRead(_pinoEcho) == LOW) {
          unsigned long duracao = agora - _inicioEchoUs;
          _distanciaCm = duracao / 58.0f;  // formula padrao do HC-SR04
          _leituraValida = true;
          _semEco = false;  // esta e uma medida de verdade, nao um chute
          // BUG CORRIGIDO 2026-07-27: esta linha faltava. `_sensorRespondeu`
          // so era ligado dentro de _foraDeAlcance(), entao um sensor que
          // funciona PERFEITAMENTE - devolvendo eco de verdade toda leitura -
          // reportava `sensor_ok: false` para sempre. Foi o que apareceu na
          // telemetria de hoje: frontal em 173cm e traseiro em 102cm, os dois
          // com leitura real, os dois marcados como sensor mudo. A bandeira
          // que existe para denunciar sensor quebrado estava acusando os
          // sensores bons.
          _sensorRespondeu = true;
          // LATCH DE PRESENCA: um eco de verdade so acontece se existe um
          // modulo ligado e funcionando. A partir daqui sabemos que ele
          // ESTA la, e o silencio dele passa a significar "nada refletindo"
          // em vez de "sensor ausente" - ver _semResposta.
          _jaRespondeu = true;
          _ultimaLeituraUs = agora;
          _estagio = Estagio::OCIOSO;
        } else if (agora - _inicioEchoUs > TIMEOUT_US) {
          // O pulso SAIU normalmente e nada voltou: o sensor esta vivo, so
          // nao ha nada refletindo dentro do alcance. Isso e caminho LIVRE,
          // nao cegueira - e e o estado normal de um ultrassom numa sala
          // (parede de lado, porta aberta, espaco vazio).
          //
          // Ate 2026-07-25 este caso caia no mesmo _semLeitura() do estagio
          // anterior, entao "nao ha nada na frente" era lido como
          // "obstaculo" e o robo NUNCA saia do lugar - so andava se alguem
          // estivesse parado na frente dele, o oposto do desejado.
          //
          // TROCA CONSCIENTE: um objeto macio ou muito inclinado (cortina,
          // sofa, parede de lado) tambem nao devolve eco e sera reportado
          // como "livre". Ultrassom e fisicamente cego para isso; quem
          // cobre essa falha e o impacto do IMU e a camada tatica do Pi.
          _foraDeAlcance(agora);
        }
        break;
    }
  }

  float distanciaCm() const { return _distanciaCm; }
  bool leituraValida() const { return _leituraValida; }
  //: false so quando o modulo nao respondeu ao trigger (defeito de verdade).
  //: "Nada dentro do alcance" NAO derruba isto - ver _foraDeAlcance.
  bool sensorRespondeu() const { return _sensorRespondeu; }
  //: true quando a distancia atual NAO foi medida: o pulso saiu, nada voltou,
  //: e o valor reportado e o teto de alcance (ver _foraDeAlcance). Existe
  //: para que ninguem confunda esse numero com uma medicao - de fora, 517cm
  //: "valido" era indistinguivel de uma parede realmente a 517cm.
  bool semEco() const { return _semEco; }

 private:
  enum class Estagio { OCIOSO, AGUARDANDO_SUBIDA, AGUARDANDO_DESCIDA };
  static constexpr unsigned long INTERVALO_ENTRE_LEITURAS_US = 60000;  // 60ms (datasheet)
  static constexpr unsigned long TIMEOUT_US = 30000;  // ~5m de alcance maximo

  uint8_t _pinoTrig = 0;
  uint8_t _pinoEcho = 0;
  Estagio _estagio = Estagio::OCIOSO;
  unsigned long _ultimaLeituraUs = 0;
  unsigned long _inicioEsperaUs = 0;
  unsigned long _inicioEchoUs = 0;
  float _distanciaCm = -1;
  bool _leituraValida = false;
  // comeca false de proposito: antes da primeira leitura nao sabemos se ha
  // sensor ligado, e o conservador e assumir que nao (o robo fica parado
  // ate o primeiro eco confirmar que o modulo existe).
  bool _sensorRespondeu = false;
  //: comeca true porque antes da primeira leitura nao existe eco nenhum - o
  //: valor inicial de _distanciaCm (-1) tambem nao e medicao.
  bool _semEco = true;
  //: fecha no primeiro eco de verdade e nunca mais abre - e a prova de que
  //: existe um modulo ligado nestes pinos. Ver _semResposta.
  bool _jaRespondeu = false;

  //: distancia reportada quando nada reflete dentro do alcance. Derivada do
  //: proprio TIMEOUT_US (30 ms / 58 = ~517 cm) para os dois nunca saírem de
  //: sincronia se alguem ajustar o timeout.
  static constexpr float ALCANCE_MAXIMO_CM = TIMEOUT_US / 58.0f;

  void _semResposta(unsigned long agora) {
    // O pino ECHO nem subiu. Isso tem DOIS significados possiveis, e o latch
    // de presenca e o que os separa:
    //
    //  - modulo nunca respondeu desde o boot -> assumimos ausente. Leitura
    //    invalida, o SafetyManager para o robo (Cap 18: em duvida, para).
    //  - modulo JA respondeu antes -> ele existe e funciona; este silencio e
    //    "nada refletindo dentro do alcance", ou seja, caminho livre.
    //
    // Sem essa distincao o robo ficava num beco sem saida: o sensor so
    // produz leitura com algo na frente, e o robo so pode andar sem nada na
    // frente - entao ele nunca andava (medido em 2026-07-25, 0 leituras
    // validas em 10 amostras com o caminho livre).
    //
    // Seguro porque um sensor de verdade ausente NUNCA fecha o latch: nao ha
    // como forjar um eco sem modulo ligado.
    if (_jaRespondeu) {
      _foraDeAlcance(agora);
      return;
    }
    _leituraValida = false;
    _sensorRespondeu = false;
    _ultimaLeituraUs = agora;
    _estagio = Estagio::OCIOSO;
  }

  void _foraDeAlcance(unsigned long agora) {
    // "Nada por perto": o sensor respondeu, so nao ha eco.
    //
    // ATENCAO - este valor NAO E UMA MEDICAO. Ninguem mediu 517 cm; o pulso
    // saiu, nada voltou, e o teto de alcance e reportado no lugar. Continua
    // valendo como "livre" para a seguranca reativa DE PROPOSITO: tratar
    // silencio como obstaculo travava o robo para sempre numa sala vazia
    // (medido em 2026-07-25, 0 leituras validas em 10 amostras com o caminho
    // livre). Mas o `_semEco` abaixo deixa isso explicito na telemetria, para
    // que o mapa e a navegacao do Pi possam tratar como DESCONHECIDO em vez
    // de engolir 517 cm como se fosse parede medida (EDR-0024).
    _distanciaCm = ALCANCE_MAXIMO_CM;
    _leituraValida = true;
    _semEco = true;
    _sensorRespondeu = true;
    _ultimaLeituraUs = agora;
    _estagio = Estagio::OCIOSO;
  }
};

}  // namespace orion
