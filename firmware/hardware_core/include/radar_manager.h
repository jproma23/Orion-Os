// Radar Manager (Cap 10 secao 6) - radar frontal (SCAN_FRONT).
//
// CONFIRMADO 2026-07-18: SERVO_RADAR fisicamente montado - a varredura
// abaixo agora move o sensor de verdade, cada angulo le uma distancia
// fisica diferente. distanciaFrontalCm() (sem varredura, angulo atual do
// servo) e o que o Safety Manager usa para obstaculo frontal.
#pragma once

#include <Arduino.h>
// Ver a nota em command_executor.h: <Servo.h> e do AVR, ESP32Servo tem a
// mesma API e o mesmo nome de classe.
#include <ESP32Servo.h>

#include "pins.h"
#include "sensor_ultrassonico.h"

namespace orion {

constexpr uint8_t RADAR_QUANTIDADE_ANGULOS = 7;

struct LeituraRadar {
  uint8_t angulo;
  float distanciaCm;
  bool valida;
};

class RadarManager {
 public:
  void iniciar() {
    _ultrassom.iniciar(pinos::ULTRASSOM_FRENTE_TRIG, pinos::ULTRASSOM_FRENTE_ECHO);
    _servo.attach(pinos::SERVO_RADAR);
    _servo.write(_paraFisico(90));
    _anguloAtual = 90;
  }

  void atualizar() {
    _ultrassom.atualizar();
    // Memoria da ultima leitura FRONTAL boa (achado medido em 2026-07-28):
    // a varredura desvia ESTE MESMO sensor por ~2,1s, e ate 28/07 esse
    // intervalo inteiro contava como obstaculo. Nao e obstaculo: a frente
    // nao ficou desconhecida, ficou nao-observada por um instante. Guardar
    // a ultima medida boa COM A HORA dela deixa a seguranca decidir com
    // informacao real em vez de com o pior caso imaginario.
    if (apontandoParaFrente() && _ultrassom.leituraValida()) {
      _ultimaFrontalCm = _ultrassom.distanciaCm();
      _ultimaFrontalMs = millis();
      _temUltimaFrontal = true;
    }
    if (_varrendo) _atualizarVarredura();
  }

  void iniciarVarredura() {
    _varrendo = true;
    _indiceAtual = 0;
    _comandarServo(_angulos[0]);
  }

  // Comando de calibracao manual (fora do fluxo de SCAN_FRONT): move so o
  // servo do radar pra um angulo especifico, sem ativar _varrendo (nao
  // produz motion.scan_complete). Usado so pra achar o arco de rotacao
  // livre na montagem fisica - achado real: colisao com o suporte da
  // webcam ao lado, vistoria de 2026-07-24.
  void moverServoTeste(uint8_t angulo) { _comandarServo(angulo); }

  bool varrendo() const { return _varrendo; }
  const LeituraRadar* leituras() const { return _leituras; }
  uint8_t quantidadeLeituras() const { return RADAR_QUANTIDADE_ANGULOS; }

  float distanciaFrontalCm() const { return _ultrassom.distanciaCm(); }
  bool distanciaFrontalValida() const { return _ultrassom.leituraValida(); }
  //: false = o modulo nao respondeu ao trigger (ausente, fio solto, sem 5V).
  //: Distingue isso de "respondeu mas nada refletiu" - ver sensor_ultrassonico.h.
  bool sensorFrontalRespondeu() const { return _ultrassom.sensorRespondeu(); }
  //: true quando a distancia frontal atual nao foi medida (pulso saiu, nada
  //: voltou) - ver SensorUltrassonico::semEco.
  bool frontalSemEco() const { return _ultrassom.semEco(); }
  //: amostras descartadas por o loop ter ficado cego durante o eco - ver
  //: SensorUltrassonico::descartes.
  unsigned long frontalDescartes() const { return _ultrassom.descartes(); }
  //: para onde o radar aponta AGORA (angulo logico, 90 = frente). Sem ele a
  //: distancia frontal e um numero sem contexto e o mapa nao pode ser montado.
  uint8_t anguloAtual() const { return _anguloAtual; }
  // Achado real da vistoria de 2026-07-24: durante um SCAN_FRONT (~2,1s), o
  // MESMO sensor ultrassonico usado aqui fica apontado pro lado boa parte
  // do tempo (varredura 0/30/60/90/120/150/180 graus) - sem isso, o Safety
  // Manager confiava na leitura como se fosse sempre frontal, criando um
  // ponto cego de seguranca durante a varredura.
  bool apontandoParaFrente() const { return _anguloAtual == 90; }
  //: true se existe uma leitura frontal DE VERDADE (radar em 90 e leitura
  //: valida) com menos de `janelaMs` de idade. E o que permite distinguir
  //: "tem coisa na frente" de "estou olhando pro lado agora".
  bool temFrontalRecente(unsigned long janelaMs) const {
    return _temUltimaFrontal && (millis() - _ultimaFrontalMs) <= janelaMs;
  }
  float frontalRecenteCm() const { return _ultimaFrontalCm; }
  //: idade da ultima leitura frontal boa; 0xFFFFFFFF se nunca houve uma.
  unsigned long idadeFrontalMs() const {
    return _temUltimaFrontal ? (millis() - _ultimaFrontalMs) : 0xFFFFFFFFUL;
  }

 private:
  // membro de instancia normal (nao static) - evita a exigencia de uma
  // definicao fora da classe que um array "static constexpr" teria no
  // padrao C++ usado por este toolchain AVR.
  // Arco de 120 graus (30..150), centrado na frente - confirmado ao vivo
  // com o usuario apos a calibracao do offset abaixo como suficiente pra
  // enxergar o mundo a frente sem colidir com o suporte da webcam ao lado
  // (vistoria de 2026-07-24). Ajustar aqui se o suporte for reposicionado.
  uint8_t _angulos[RADAR_QUANTIDADE_ANGULOS] = {30, 50, 70, 90, 110, 130, 150};
  static constexpr unsigned long TEMPO_ASSENTAMENTO_SERVO_MS = 300;
  // Offset de calibracao fisica (Cap 10 s.6, achado real 2026-07-24): o
  // parafuso do horn deste servo esta colado torto e nao da pra reencaixar
  // fisicamente - calibrado ao vivo com o usuario (camera apontada pro
  // sensor): mandar o servo pro angulo LOGICO 90 (frente) so fica reto de
  // verdade escrevendo 45 de verdade no servo. Esse offset e aplicado so
  // na hora de escrever no servo (_paraFisico) - toda a logica interna
  // (safety, indices de varredura, _anguloAtual, RADAR_SET_ANGLE) continua
  // trabalhando 100% em angulo LOGICO (0-180, 90=frente), sem precisar
  // saber do desalinhamento fisico. Se o horn for descolado/refeito um
  // dia, zerar este valor.
  static constexpr int RADAR_OFFSET_GRAUS = -45;

  SensorUltrassonico _ultrassom;
  Servo _servo;
  bool _varrendo = false;
  bool _aguardandoServo = false;
  uint8_t _indiceAtual = 0;
  unsigned long _momentoComandoServo = 0;
  uint8_t _anguloAtual = 90;
  float _ultimaFrontalCm = 0.0f;
  unsigned long _ultimaFrontalMs = 0;
  bool _temUltimaFrontal = false;
  LeituraRadar _leituras[RADAR_QUANTIDADE_ANGULOS];

  static uint8_t _paraFisico(uint8_t anguloLogico) {
    return static_cast<uint8_t>(constrain((int)anguloLogico + RADAR_OFFSET_GRAUS, 0, 180));
  }

  void _comandarServo(uint8_t angulo) {
    _servo.write(_paraFisico(angulo));
    _anguloAtual = angulo;
    _momentoComandoServo = millis();
    _aguardandoServo = true;
  }

  void _atualizarVarredura() {
    if (_aguardandoServo) {
      if (millis() - _momentoComandoServo < TEMPO_ASSENTAMENTO_SERVO_MS) return;
      _aguardandoServo = false;
    }

    uint8_t angulo = _angulos[_indiceAtual];
    _leituras[_indiceAtual] = {angulo, _ultrassom.distanciaCm(), _ultrassom.leituraValida()};

    _indiceAtual++;
    if (_indiceAtual >= RADAR_QUANTIDADE_ANGULOS) {
      _varrendo = false;
      _comandarServo(90);
      return;
    }
    _comandarServo(_angulos[_indiceAtual]);
  }
};

}  // namespace orion
