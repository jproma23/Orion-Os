// Command Executor (Cap 10 secao 10) - traduz COMMAND em acao nos managers.
//
// So a logica de despacho; enquadramento/ACK/RESPONSE continuam em
// main.cpp (ja existentes desde a Fase 2), para nao duplicar
// responsabilidade de protocolo aqui.
#pragma once

#include <ArduinoJson.h>
#include <Servo.h>
#include <string.h>

#include "estado.h"
#include "motor_manager.h"
#include "pins.h"
#include "radar_manager.h"

namespace orion {

// SET_PAN_TILT nao estava na lista original do Cap 10 s.5, mas o hardware
// (servos pan/tilt) esta no Cap 10 s.2 e o Cap 8 s.8 exige o comando para
// centralizar o alvo da visao computacional - extensao justificada, nao
// uma mudanca de arquitetura (mesmo protocolo, mesmo formato de payload).
constexpr float SERVO_CENTRO_GRAUS = 90.0f;

class CommandExecutor {
 public:
  CommandExecutor(MotorManager& motores, RadarManager& radar, MaquinaDeEstados& estados)
      : _motores(motores), _radar(radar), _estados(estados) {}

  // Precisa ser chamado de dentro de setup(), nunca no construtor: este
  // objeto e global (criado antes de main()/init() rodar), e Servo::attach()
  // configura um timer de hardware que o init() do Arduino ainda vai
  // reconfigurar em seguida - se attach() rodasse no construtor, o init()
  // pisaria por cima e o servo nunca responderia, mesmo com o codigo
  // "parecendo" correto (mesmo motivo de motores.iniciar()/radar.iniciar()
  // existirem em vez de fazer isso no construtor deles).
  void iniciar() {
    pinMode(pinos::LED_LANTERNA, OUTPUT);
    _servoPan.attach(pinos::SERVO_PAN);
    _servoTilt.attach(pinos::SERVO_TILT);
    _servoPan.write(SERVO_CENTRO_GRAUS);
    _servoTilt.write(SERVO_CENTRO_GRAUS);
  }

  // Retorna true se reconheceu e executou o comando (main.cpp so precisa
  // saber se deve considerar "executado" para fins de log/telemetria).
  bool executar(const char* comando, JsonObjectConst payload) {
    float velocidade = payload["velocidade_percent"] | 50.0f;

    if (strcmp(comando, "MOVE_FORWARD") == 0 || strcmp(comando, "MOVE_CONTINUOUS") == 0) {
      _motores.andarFrente(velocidade);
      _estados.transicionarPara(Estado::EXECUTING_MISSION);
      return true;
    }

    if (strcmp(comando, "MOVE_DISTANCE") == 0) {
      float distancia = payload["distancia_cm"] | 0.0f;
      _motores.andarDistancia(distancia, velocidade);
      _estados.transicionarPara(Estado::EXECUTING_MISSION);
      return true;
    }

    if (strcmp(comando, "TURN_LEFT") == 0 || strcmp(comando, "TURN_RIGHT") == 0) {
      bool horario = strcmp(comando, "TURN_RIGHT") == 0;
      if (payload["graus"].is<float>()) {
        _motores.girarGraus(payload["graus"].as<float>(), horario, velocidade);
      } else {
        _motores.girarContinuo(horario, velocidade);
      }
      _estados.transicionarPara(Estado::EXECUTING_MISSION);
      return true;
    }

    if (strcmp(comando, "STOP") == 0) {
      _motores.parar();
      _estados.transicionarPara(Estado::IDLE);
      return true;
    }

    if (strcmp(comando, "DOCK") == 0) {
      // Fase minima: so para o robo. Alinhamento fino por sensores de
      // docking dedicados fica para uma fase futura (fora do Cap 10 s.5
      // "minimo" atual).
      _motores.parar();
      _estados.transicionarPara(Estado::IDLE);
      return true;
    }

    if (strcmp(comando, "SCAN_FRONT") == 0) {
      _radar.iniciarVarredura();
      return true;
    }

    if (strcmp(comando, "LIGHT_ON") == 0) {
      digitalWrite(pinos::LED_LANTERNA, HIGH);
      return true;
    }

    if (strcmp(comando, "LIGHT_OFF") == 0) {
      digitalWrite(pinos::LED_LANTERNA, LOW);
      return true;
    }

    if (strcmp(comando, "SET_PAN_TILT") == 0) {
      // pan_graus/tilt_graus chegam relativos ao centro (-limite..+limite,
      // calculados pelo Vision Core - Cap 8 s.8); o servo fisico espera
      // 0-180 com centro em 90.
      float panGraus = payload["pan_graus"] | 0.0f;
      float tiltGraus = payload["tilt_graus"] | 0.0f;
      _servoPan.write(constrain(SERVO_CENTRO_GRAUS + panGraus, 0, 180));
      _servoTilt.write(constrain(SERVO_CENTRO_GRAUS + tiltGraus, 0, 180));
      return true;
    }

    return false;  // WHO_ARE_YOU/RETURN_STATUS ja tratados em main.cpp
  }

 private:
  MotorManager& _motores;
  RadarManager& _radar;
  MaquinaDeEstados& _estados;
  Servo _servoPan;
  Servo _servoTilt;
};

}  // namespace orion
