// Telemetry Manager (Cap 10 secao 8) - pacote periodico Radar Inteligente
// (Cap 5 secao 7): distancias dos ultrassons, orientacao do MPU, estado
// dos motores, velocidade estimada, temperatura e umidade.
#pragma once

#include <ArduinoJson.h>

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
                    BateriaManager& bateria)
      : _motores(motores),
        _radar(radar),
        _imu(imu),
        _dht(dht),
        _estados(estados),
        _ultrassomTraseiro(ultrassomTraseiro),
        _bateria(bateria) {}

  void preencherPayload(JsonObject destino) {
    destino["estado"] = nomeEstado(_estados.atual());

    destino["distancia_frontal_cm"] = _radar.distanciaFrontalCm();
    destino["distancia_frontal_valida"] = _radar.distanciaFrontalValida();

    destino["distancia_traseira_cm"] = _ultrassomTraseiro.distanciaCm();
    destino["distancia_traseira_valida"] = _ultrassomTraseiro.leituraValida();

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
};

}  // namespace orion
