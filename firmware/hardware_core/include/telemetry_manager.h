// Telemetry Manager (Cap 10 secao 8) - pacote periodico Radar Inteligente
// (Cap 5 secao 7): distancias dos ultrassons, orientacao do MPU, estado
// dos motores, velocidade estimada, temperatura e umidade.
#pragma once

#include <ArduinoJson.h>

#include "bussola_manager.h"
#include "dht_manager.h"
#include "encoder_manager.h"
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
                    BateriaManager& bateria, BussolaManager& bussola,
                    EncoderManager& encoders)
      : _motores(motores),
        _radar(radar),
        _imu(imu),
        _dht(dht),
        _estados(estados),
        _ultrassomTraseiro(ultrassomTraseiro),
        _bateria(bateria),
        _bussola(bussola),
        _encoders(encoders) {}

  // Pacote REDUZIDO, para quando o robo esta em movimento (2026-07-27).
  //
  // Motivo medido: o pacote completo tem ~1850 bytes com enquadramento e o
  // buffer de saida do AVR e de 64, entao `Serial.write` bloqueia 74 ms
  // esperando drenar. Nesse tempo o loop nao olha o pino ECHO do ultrassom,
  // e o eco de um obstaculo a 30 cm dura 1,7 ms - o eco inteiro cabe dentro
  // da cegueira. Pior: o sensor conclui "nada refletiu" e reporta caminho
  // LIVRE, que e o lado errado para falhar, justo quando o robo anda.
  //
  // Aqui vai SO o que a navegacao e a seguranca precisam a cada ciclo.
  // Temperatura, bateria, limiar de impacto e diagnostico nao mudam em
  // 200 ms e voltam no pacote completo assim que o robo parar.
  void preencherPayloadReduzido(JsonObject destino) {
    destino["estado"] = nomeEstado(_estados.atual());
    destino["reduzido"] = true;  // o Pi precisa saber que os campos ausentes
                                  // sao omissao proposital, nao sensor mudo

    destino["distancia_frontal_cm"] = _radar.distanciaFrontalCm();
    destino["distancia_frontal_valida"] = _radar.distanciaFrontalValida();
    destino["frontal_sensor_ok"] = _radar.sensorFrontalRespondeu();
    destino["frontal_sem_eco"] = _radar.frontalSemEco();
    destino["radar_angulo_graus"] = _radar.anguloAtual();

    destino["distancia_traseira_cm"] = _ultrassomTraseiro.distanciaCm();
    destino["distancia_traseira_valida"] = _ultrassomTraseiro.leituraValida();
    destino["traseiro_sem_eco"] = _ultrassomTraseiro.semEco();

    // Seguranca: inclinacao e impacto sao os dois gatilhos de SAFE_MODE que
    // dependem de dado fresco enquanto o robo se move.
    if (_imu.conectado()) {
      destino["inclinacao_graus"] = _imu.inclinacaoGraus();
      destino["impacto_detectado"] = _imu.impactoDetectado();
    }

    // Rumo entra porque a odometria de rotacao se apoia nele (EDR-0024).
    if (_bussola.rumoValido()) {
      destino["rumo_graus"] = _bussola.rumoGraus();
    }

    destino["passos_esquerda"] = _motores.passosAcumuladosEsquerda();
    destino["passos_direita"] = _motores.passosAcumuladosDireita();
    destino["em_movimento"] = _motores.emMovimento();

    // Encoder e yaw entram no pacote REDUZIDO, nao so no completo: os
    // dois servem a odometria, e odometria so importa enquanto o robo
    // anda - que e exatamente quando o pacote completo NAO e enviado.
    // Sao 3 campos, longe de recriar o bloqueio de serial que motivou
    // este pacote reduzido existir.
    preencherOdometria(destino);
  }

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

    preencherOdometria(destino);
  }

  // Campos que a odometria do Motion Core consome (fusao_sensores.py).
  // Compartilhado pelos dois pacotes para os nomes nao divergirem: um
  // campo com nome diferente entre reduzido e completo apareceria como
  // "encoder que some quando o robo para", que e o tipo de sintoma que
  // custa uma sessao inteira para achar.
  void preencherOdometria(JsonObject destino) {
    // Publicados SEMPRE, mesmo sem encoder fisico ligado - ficam em zero, e
    // quem decide se isso vale alguma coisa e o `motion.encoder_lado` do
    // config, no Motion Core. O firmware nao tem como saber se ha sensor no
    // pino; fingir que sabe seria inventar.
    destino["pulsos_esquerda"] = _encoders.pulsosEsquerdo();
    destino["pulsos_direita"] = _encoders.pulsosDireito();

    // yaw so vai quando VALE: antes do vies do giroscopio ser estimado o
    // numero e ruido integrado. Omitir e melhor que mandar - o
    // fusao_sensores.py trata campo ausente caindo para a fonte pior e
    // dizendo isso no `fonte_rumo`, enquanto um numero errado ele nao tem
    // como perceber.
    if (_imu.yawValido()) {
      destino["yaw_graus"] = _imu.yawGraus();
    }
  }

 private:
  MotorManager& _motores;
  EncoderManager& _encoders;
  RadarManager& _radar;
  ImuManager& _imu;
  DhtManager& _dht;
  MaquinaDeEstados& _estados;
  SensorUltrassonico& _ultrassomTraseiro;
  BateriaManager& _bateria;
  BussolaManager& _bussola;
};

}  // namespace orion
