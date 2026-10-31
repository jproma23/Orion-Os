// Telemetry Manager (Cap 10 secao 8) - pacote periodico Radar Inteligente
// (Cap 5 secao 7): orientacao da bussola, estado
// dos motores, velocidade estimada, temperatura e umidade.
#pragma once

#include <ArduinoJson.h>

#include "bateria_manager.h"
#include "bussola_manager.h"
#include "dht_manager.h"
#include "estado.h"
#include "motor_manager.h"
#include "safety_manager.h"

namespace orion {

class TelemetryManager {
 public:
  // SEM DISTANCIA (2026-09-19): os ultrassons sairam do robo e o LiDAR fala
  // direto com o Raspberry. Este pacote perdeu TODOS os campos de distancia
  // - `distancia_frontal_cm`, `distancia_traseira_cm`, os `*_valida`, os
  // `*_sem_eco`, os `*_descartes` e os `radar_*`. Quem lia distancia daqui
  // (NavigationCore, EstadoDoMundo) le do `lidar.scan` desde a mesma data.
  //
  // Os campos foram REMOVIDOS, nao zerados: um `distancia_frontal_cm: 0` ou
  // `-1` viajando no pacote seria lido como medida por qualquer consumidor
  // distraido, e esse e o erro que este projeto ja pagou caro - "nao medi"
  // entregue como numero.
  TelemetryManager(MotorManager& motores, DhtManager& dht,
                    MaquinaDeEstados& estados,
                    BateriaManager& bateria, BussolaManager& bussola, SafetyManager& safety)
      : _safety(safety),
        _motores(motores),
        _dht(dht),
        _estados(estados),
        _bateria(bateria),
        _bussola(bussola) {}

  // Pacote REDUZIDO, para quando o robo esta em movimento (2026-07-27).
  //
  // Motivo medido (na epoca do Mega e dos ultrassons): o pacote completo tem
  // ~1850 bytes com enquadramento e o buffer de saida do AVR era de 64, entao
  // `Serial.write` bloqueava 74 ms esperando drenar - e nesse tempo o loop nao
  // olhava o pino ECHO, cujo eco a 30 cm dura 1,7 ms. O sensor concluia "nada
  // refletiu" e reportava caminho LIVRE, o lado errado para falhar.
  //
  // Aquele sensor nao existe mais, mas o pacote reduzido FICA: pacote menor
  // continua sendo menos tempo de UART ocupada, e e nessa janela que os
  // heartbeats atrasam - que e como o sistema detecta link caido.
  //
  // Aqui vai SO o que a navegacao e a seguranca precisam a cada ciclo.
  // Temperatura, bateria e diagnostico nao mudam em 200 ms e voltam no
  // pacote completo assim que o robo parar.
  void preencherPayloadReduzido(JsonObject destino) {
    destino["estado"] = nomeEstado(_estados.atual());
    destino["reduzido"] = true;  // o Pi precisa saber que os campos ausentes
                                  // sao omissao proposital, nao sensor mudo

    // POR QUE a seguranca parou o robo. Ate 28/07 isto nao existia na
    // telemetria e o Pi via o estado sem saber a razao - custou uma sessao
    // inteira de diagnostico as cegas. Hoje o unico motivo possivel e o
    // homem-morto, mas o campo fica: o Pi nao deve supor o motivo.
    destino["seguranca_parada_ativa"] = _safety.pararAtivo();
    if (_safety.pararAtivo()) destino["seguranca_motivo"] = _safety.motivo();

    // Rumo entra porque a odometria de rotacao se apoia nele (EDR-0024).
    if (_bussola.rumoValido()) {
      destino["rumo_graus"] = _bussola.rumoGraus();
    }

    destino["passos_esquerda"] = _motores.passosAcumuladosEsquerda();
    destino["passos_direita"] = _motores.passosAcumuladosDireita();
    destino["em_movimento"] = _motores.emMovimento();
    // A malha que mantem a reta (2026-10-31): quem ela segue e quanto o
    // robo desviou. Desvio grande so com "encoder" = uma roda mais fraca;
    // com "bussola+encoder" e as rodas iguais = o robo escorregou.
    destino["reta_fonte"] = _motores.fonteReta();
    destino["reta_desvio_graus"] = _motores.desvioRetaGraus();
    destino["roda_travada"] = _motores.rodaTravada();
  }

  void preencherPayload(JsonObject destino) {
    destino["estado"] = nomeEstado(_estados.atual());

    // TODOS OS CAMPOS DE DISTANCIA SAIRAM DAQUI em 2026-09-19, junto com os
    // ultrassons. Eram 13 chaves: as duas distancias, os `*_valida`, os
    // `*_sensor_ok`, os `*_sem_eco`, os `*_descartes`, `frontal_idade_ms` e
    // os tres `radar_*`.
    //
    // O que cada uma resolvia NAO se perdeu - migrou de lugar, porque o
    // problema era do sensor, nao do campo:
    //   `*_sem_eco`      -> setor com `distancia_cm: null` no `lidar.scan`
    //   `*_sensor_ok`    -> `lidar.scan` chegando (vivo) x `lidar.error`
    //   `*_descartes`    -> `setores_cobertos` de `setores_total`
    //   `radar_angulo_*` -> cada setor ja traz `inicio_graus`/`fim_graus`
    //
    // A licao que sobrevive aos dois sensores: "nao medi" e "medi longe" tem
    // que ser distinguiveis de fora. Confundir os dois manteve o robo parado
    // por horas em 25/07 e o fez andar as cegas em 29/07.
    destino["dht_valido"] = _dht.leituraValida();
    if (_dht.leituraValida()) {
      destino["temperatura_c"] = _dht.temperaturaC();
      destino["umidade_percent"] = _dht.umidadePercent();
    }

    // Bateria: so entra na telemetria quando o divisor ja foi montado
    // (sem divisor, A0 flutua e o valor nao significa nada).
    destino["bateria_lida"] = _bateria.lida();
    if (_bateria.lida()) {
      destino["bateria_tensao_v"] = _bateria.tensaoV();
      destino["bateria_percent"] = _bateria.percentualEstimado();
      destino["bateria_nivel"] = BateriaManager::nomeNivel(_bateria.nivel());
    }

    // Bussola (QMC6310). `rumo_valido` separa "modulo ausente" de "presente
    // mas ainda sem calibracao" - sem calibracao o rumo existe mas nao
    // significa nada, e a fusao de sensores precisa saber a diferenca para
    // nao corrigir a odometria com um numero inventado.
    destino["bussola_conectada"] = _bussola.conectado();
    if (_bussola.conectado()) {
      destino["bussola_calibrada"] = _bussola.calibrada();
      destino["bussola_calibrando"] = _bussola.calibrando();
      destino["bussola_eixos"] = _bussola.eixosRumo();
      destino["bussola_volta_calibracao_graus"] = _bussola.voltaCalibracaoGraus();
      destino["rumo_valido"] = _bussola.rumoValido();
      destino["rumo_graus"] = _bussola.rumoGraus();
      // Deveria ficar parado por mais que o robo gire - oscilacao grande
      // aqui denuncia calibracao ruim ou motor ligado perto demais.
      destino["campo_ut"] = _bussola.campoUt();
    }

    destino["passos_esquerda"] = _motores.passosAcumuladosEsquerda();
    destino["passos_direita"] = _motores.passosAcumuladosDireita();
    destino["em_movimento"] = _motores.emMovimento();
    // A malha que mantem a reta (2026-10-31): quem ela segue e quanto o
    // robo desviou. Desvio grande so com "encoder" = uma roda mais fraca;
    // com "bussola+encoder" e as rodas iguais = o robo escorregou.
    destino["reta_fonte"] = _motores.fonteReta();
    destino["reta_desvio_graus"] = _motores.desvioRetaGraus();
    destino["roda_travada"] = _motores.rodaTravada();
  }

 private:
  SafetyManager& _safety;
  MotorManager& _motores;
  DhtManager& _dht;
  MaquinaDeEstados& _estados;
  BateriaManager& _bateria;
  BussolaManager& _bussola;
};

}  // namespace orion
