// Telemetry Manager (Cap 10 secao 8) - pacote periodico Radar Inteligente
// (Cap 5 secao 7): distancias dos ultrassons, orientacao do MPU, estado
// dos motores, velocidade estimada, temperatura e umidade.
#pragma once

#include <ArduinoJson.h>

#include "bussola_manager.h"
#include "dht_manager.h"
#include "estado.h"
#include "bateria_manager.h"
#include "imu_manager.h"
#include "motor_manager.h"
#include "radar_manager.h"
#include "sensor_ultrassonico.h"

namespace orion {

class TelemetryManager {
 public:
  TelemetryManager(MotorManager& motores, RadarManager& radar, ImuManager& imu, DhtManager& dht,
                    MaquinaDeEstados& estados, SensorUltrassonico& ultrassomTraseiro,
                    BateriaManager& bateria, BussolaManager& bussola)
      : _motores(motores),
        _radar(radar),
        _imu(imu),
        _dht(dht),
        _estados(estados),
        _ultrassomTraseiro(ultrassomTraseiro),
        _bateria(bateria),
        _bussola(bussola) {}

  void preencherPayload(JsonObject destino) {
    destino["estado"] = nomeEstado(_estados.atual());

    destino["distancia_frontal_cm"] = _radar.distanciaFrontalCm();
    destino["distancia_frontal_valida"] = _radar.distanciaFrontalValida();
    // sensor_ok separa "modulo ausente/mudo" de "nada refletindo dentro do
    // alcance". Sem este campo os dois casos sao indistinguiveis de fora, e
    // foi exatamente essa confusao que manteve o robo parado por horas em
    // 2026-07-25. false = o pino ECHO nem chegou a subir apos o trigger.
    destino["frontal_sensor_ok"] = _radar.sensorFrontalRespondeu();
    // `sem_eco` = a distancia acima NAO foi medida, e o teto de alcance
    // reportado porque nada refletiu. Sem este campo, "517 cm valido" era
    // indistinguivel de uma parede realmente a 517 cm - quem monta mapa
    // precisa tratar um como DESCONHECIDO e o outro como obstaculo (EDR-0024).
    destino["frontal_sem_eco"] = _radar.frontalSemEco();

    destino["distancia_traseira_cm"] = _ultrassomTraseiro.distanciaCm();
    destino["distancia_traseira_valida"] = _ultrassomTraseiro.leituraValida();
    destino["traseiro_sensor_ok"] = _ultrassomTraseiro.sensorRespondeu();
    destino["traseiro_sem_eco"] = _ultrassomTraseiro.semEco();

    // Angulo para onde o radar aponta. Sem isto, a distancia frontal e um
    // numero sem significado (nao se sabe o que foi medido), e o mapa nunca
    // pode ser construido - por isso mapa.leituras vive vazio.
    destino["radar_angulo_graus"] = _radar.anguloAtual();

    destino["imu_conectado"] = _imu.conectado();
    if (_imu.conectado()) {
      destino["imu_calibrado"] = _imu.calibrado();
      destino["inclinacao_graus"] = _imu.inclinacaoGraus();
      destino["impacto_detectado"] = _imu.impactoDetectado();
      destino["aceleracao_g"] = _imu.aceleracaoG();
      destino["pico_g"] = _imu.consumirPicoG();
      destino["limite_impacto_g"] = _imu.limiteImpactoG();
    }

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
      destino["rumo_valido"] = _bussola.rumoValido();
      destino["rumo_graus"] = _bussola.rumoGraus();
      // Deveria ficar parado por mais que o robo gire - oscilacao grande
      // aqui denuncia calibracao ruim ou motor ligado perto demais.
      destino["campo_ut"] = _bussola.campoUt();
    }

    destino["passos_esquerda"] = _motores.passosAcumuladosEsquerda();
    destino["passos_direita"] = _motores.passosAcumuladosDireita();
    destino["em_movimento"] = _motores.emMovimento();
  }

 private:
  MotorManager& _motores;
  RadarManager& _radar;
  ImuManager& _imu;
  DhtManager& _dht;
  MaquinaDeEstados& _estados;
  SensorUltrassonico& _ultrassomTraseiro;
  BateriaManager& _bateria;
  BussolaManager& _bussola;
};

}  // namespace orion
