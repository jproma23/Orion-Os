// Telemetry Manager (Cap 10 secao 8) - pacote periodico Radar Inteligente
// (Cap 5 secao 7): distancias dos ultrassons, orientacao do MPU, estado
// dos motores, velocidade estimada, temperatura e umidade.
#pragma once

#include <ArduinoJson.h>

#include "dht_manager.h"
#include "estado.h"
#include "imu_manager.h"
#include "motor_manager.h"
#include "radar_manager.h"
#include "sensor_ultrassonico.h"

namespace orion {

class TelemetryManager {
 public:
  TelemetryManager(MotorManager& motores, RadarManager& radar, ImuManager& imu, DhtManager& dht,
                    MaquinaDeEstados& estados, SensorUltrassonico& ultrassomTraseiro)
      : _motores(motores),
        _radar(radar),
        _imu(imu),
        _dht(dht),
        _estados(estados),
        _ultrassomTraseiro(ultrassomTraseiro) {}

  void preencherPayload(JsonObject destino) {
    destino["estado"] = nomeEstado(_estados.atual());

    destino["distancia_frontal_cm"] = _radar.distanciaFrontalCm();
    destino["distancia_frontal_valida"] = _radar.distanciaFrontalValida();
    // sensor_ok separa "modulo ausente/mudo" de "nada refletindo dentro do
    // alcance". Sem este campo os dois casos sao indistinguiveis de fora, e
    // foi exatamente essa confusao que manteve o robo parado por horas em
    // 2026-07-25. false = o pino ECHO nem chegou a subir apos o trigger.
    destino["frontal_sensor_ok"] = _radar.sensorFrontalRespondeu();

    destino["distancia_traseira_cm"] = _ultrassomTraseiro.distanciaCm();
    destino["distancia_traseira_valida"] = _ultrassomTraseiro.leituraValida();
    destino["traseiro_sensor_ok"] = _ultrassomTraseiro.sensorRespondeu();

    // Angulo para onde o radar aponta. Sem isto, a distancia frontal e um
    // numero sem significado (nao se sabe o que foi medido), e o mapa nunca
    // pode ser construido - por isso mapa.leituras vive vazio.
    destino["radar_angulo_graus"] = _radar.anguloAtual();

    destino["imu_conectado"] = _imu.conectado();
    if (_imu.conectado()) {
      destino["inclinacao_graus"] = _imu.inclinacaoGraus();
      destino["impacto_detectado"] = _imu.impactoDetectado();
    }

    destino["dht_valido"] = _dht.leituraValida();
    if (_dht.leituraValida()) {
      destino["temperatura_c"] = _dht.temperaturaC();
      destino["umidade_percent"] = _dht.umidadePercent();
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
};

}  // namespace orion
