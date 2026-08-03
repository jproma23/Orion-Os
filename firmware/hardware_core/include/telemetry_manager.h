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
#include "safety_manager.h"
#include "sensor_ultrassonico.h"

namespace orion {

class TelemetryManager {
 public:
  TelemetryManager(MotorManager& motores, RadarManager& radar, ImuManager& imu, DhtManager& dht,
                    MaquinaDeEstados& estados, SensorUltrassonico& ultrassomTraseiro,
                    BateriaManager& bateria, BussolaManager& bussola, SafetyManager& safety,
                    EncoderManager& encoders)
      : _safety(safety),
        _encoders(encoders),
        _motores(motores),
        _radar(radar),
        _imu(imu),
        _dht(dht),
        _estados(estados),
        _ultrassomTraseiro(ultrassomTraseiro),
        _bateria(bateria),
        _bussola(bussola) {}

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
    // POR QUE a seguranca parou o robo. Ate 28/07 isto nao existia na
    // telemetria: o Pi via OBSTACLE_DETECTED e nao tinha como saber se era
    // um obstaculo de verdade ou o radar olhando pro lado. Custou uma
    // sessao inteira de diagnostico as cegas.
    destino["radar_apontando_frente"] = _radar.apontandoParaFrente();
    destino["seguranca_parada_ativa"] = _safety.pararAtivo();
    if (_safety.pararAtivo()) destino["seguranca_motivo"] = _safety.motivo();

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

    // OS PULSOS ENTRAM NO PACOTE REDUZIDO DE PROPOSITO (2026-08-02).
    //
    // Este pacote e enxuto porque cada byte aqui e tempo de olho fechado para
    // o ultrassom - a regra e "so o que a navegacao e a seguranca precisam a
    // cada ciclo". Estes dois numeros passam nessa regra pelo motivo mais
    // forte possivel: enquanto o robo ANDA e o unico momento em que a
    // odometria existe. Manda-los so no pacote completo, que so sai com o
    // robo parado, seria medir o movimento e contar depois que ele acabou.
    //
    // E o par ao lado de `passos_*` NAO e redundancia: `passos_*` e o que o
    // firmware MANDOU a roda fazer, `encoder_*` e o que a roda FEZ. A
    // diferenca entre os dois e o diagnostico mais valioso que este robo tem
    // - foi a ausencia dela que deixou a pose afirmar 85 cm com os motores
    // desligados. Passo subindo com pulso parado = roda travada, patinando ou
    // sem energia.
    destino["pulsos_esquerdo"] = _encoders.esquerdo().pulsos;
    destino["pulsos_direito"] = _encoders.direito().pulsos;
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

    // Amostras jogadas fora por o loop ter ficado cego durante o eco
    // (SensorUltrassonico::SONDAGEM_MAXIMA_US). Contadores ACUMULADOS desde o
    // boot, de proposito: o que interessa e a tendencia. Numero que sobe
    // depressa nao acusa sensor ruim - acusa quem esta bloqueando o loop
    // (hoje, o envio da telemetria). Enquanto isso subir, o mapa do EDR-0024
    // nao tem base para existir.
    destino["frontal_descartes"] = _radar.frontalDescartes();
    destino["traseiro_descartes"] = _ultrassomTraseiro.descartes();

    // Mesmo diagnostico do pacote reduzido, mais a idade da ultima leitura
    // frontal de verdade - e ela que autoriza andar durante uma varredura.
    destino["radar_apontando_frente"] = _radar.apontandoParaFrente();
    destino["frontal_idade_ms"] = _radar.idadeFrontalMs();
    destino["seguranca_parada_ativa"] = _safety.pararAtivo();
    if (_safety.pararAtivo()) destino["seguranca_motivo"] = _safety.motivo();

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

    // Encoders: sensor indutivo NPN lendo 6 chapas por roda, uma delas MAIOR
    // (ver encoder_manager.h). Aqui vai o quadro completo; o pacote reduzido
    // leva so os pulsos, que e o que a odometria consome em movimento.
    const auto& esq = _encoders.esquerdo();
    const auto& dir = _encoders.direito();
    destino["pulsos_esquerdo"] = esq.pulsos;
    destino["pulsos_direito"] = dir.pulsos;
    // Voltas confirmadas PELA CHAPA MAIOR, nao por pulsos/6. A diferenca
    // importa: pulsos/6 seria a mesma conta que ja mentia, so que com outro
    // divisor. Isto aqui e a roda dizendo "dei uma volta".
    destino["encoder_voltas_esquerda"] = esq.voltas;
    destino["encoder_voltas_direita"] = dir.voltas;
    // `marca_vista` false significa que a chapa grande ainda nao passou -
    // entao `voltas` nao vale nada e quem le PRECISA saber disso, em vez de
    // ler zero e concluir "a roda nao girou".
    destino["encoder_marca_esquerda"] = esq.marca_vista;
    destino["encoder_marca_direita"] = dir.marca_vista;
    // O TERMOMETRO DA MEDIDA. Cada vez que a chapa grande aparece com um
    // numero de pulsos diferente de 6 desde a anterior, isto sobe. Zero =
    // nenhum pulso perdido nem inventado. Subindo = ruido eletrico, chapa
    // suja ou pull-up fraco demais para o fio (ver pins.h). Sem este numero,
    // "a odometria esta confiavel?" volta a ser opiniao - que e exatamente o
    // estado de que este robo esta saindo.
    destino["encoder_desalinhados_esquerda"] = esq.desalinhamentos;
    destino["encoder_desalinhados_direita"] = dir.desalinhamentos;
    // Larguras em us: servem para escolher FATOR_LARGURA_MARCA na bancada
    // olhando numero, em vez de chutar. A marca deve sair visivelmente mais
    // larga que o pulso comum na mesma velocidade.
    destino["encoder_largura_esquerda_us"] = esq.ultima_largura_us;
    destino["encoder_largura_direita_us"] = dir.ultima_largura_us;
    destino["encoder_marca_esquerda_us"] = esq.ultima_marca_us;
    destino["encoder_marca_direita_us"] = dir.ultima_marca_us;
  }

 private:
  SafetyManager& _safety;
  MotorManager& _motores;
  RadarManager& _radar;
  ImuManager& _imu;
  DhtManager& _dht;
  MaquinaDeEstados& _estados;
  SensorUltrassonico& _ultrassomTraseiro;
  BateriaManager& _bateria;
  BussolaManager& _bussola;
  EncoderManager& _encoders;
};

}  // namespace orion
