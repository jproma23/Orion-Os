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
#include "safety_manager.h"

namespace orion {

// SET_PAN_TILT nao estava na lista original do Cap 10 s.5, mas o hardware
// (servos pan/tilt) esta no Cap 10 s.2 e o Cap 8 s.8 exige o comando para
// centralizar o alvo da visao computacional - extensao justificada, nao
// uma mudanca de arquitetura (mesmo protocolo, mesmo formato de payload).
constexpr float SERVO_CENTRO_GRAUS = 90.0f;

class CommandExecutor {
 public:
  CommandExecutor(MotorManager& motores, RadarManager& radar, MaquinaDeEstados& estados,
                   SafetyManager& safety)
      : _motores(motores), _radar(radar), _estados(estados), _safety(safety) {}

  // Precisa ser chamado de dentro de setup(), nunca no construtor: este
  // objeto e global (criado antes de main()/init() rodar), e Servo::attach()
  // configura um timer de hardware que o init() do Arduino ainda vai
  // reconfigurar em seguida - se attach() rodasse no construtor, o init()
  // pisaria por cima e o servo nunca responderia, mesmo com o codigo
  // "parecendo" correto (mesmo motivo de motores.iniciar()/radar.iniciar()
  // existirem em vez de fazer isso no construtor deles).
  void iniciar() {
    pinMode(pinos::LED_LANTERNA, OUTPUT);

    // Rele do ventilador (pins.h): fail-safe LIGADO (LOW, ativo em baixo) -
    // escreve antes do pinMode(OUTPUT) pelo mesmo motivo do rele dos
    // motores (motor_manager.h), evita glitch no pino durante o boot.
    digitalWrite(pinos::RELE_VENTILADOR, LOW);
    pinMode(pinos::RELE_VENTILADOR, OUTPUT);
    digitalWrite(pinos::RELE_VENTILADOR, LOW);

    _servoPan.attach(pinos::SERVO_PAN);
    _servoTilt.attach(pinos::SERVO_TILT);
    _servoPan.write(SERVO_CENTRO_GRAUS);
    _servoTilt.write(SERVO_CENTRO_GRAUS);
  }

  // Retorna true se reconheceu e executou o comando (main.cpp so precisa
  // saber se deve considerar "executado" para fins de log/telemetria).
  bool executar(const char* comando, JsonObjectConst payload) {
    float velocidade = payload["velocidade_percent"] | 50.0f;

    // Camada reativa (Cap 18 s.9, regra 7 do CLAUDE.md): enquanto uma
    // parada de seguranca estiver ativa (obstaculo, inclinacao, impacto
    // ou timeout), nenhum comando de movimento NOVO e aceito, mesmo que
    // venha do Raspberry - so volta a aceitar quando safety.avaliar()
    // confirmar de novo que esta livre. Sem isso, um comando
    // atrasado/reenviado (retry, fila antiga, race condition) religava o
    // motor com o perigo ainda presente (achado real da vistoria de
    // 2026-07-24) - defesa em profundidade junto com o reforco continuo
    // em SafetyManager::avaliar().
    bool comandoDeMovimento = strcmp(comando, "MOVE_FORWARD") == 0 ||
                               strcmp(comando, "MOVE_CONTINUOUS") == 0 ||
                               strcmp(comando, "MOVE_DISTANCE") == 0 ||
                               strcmp(comando, "TURN_LEFT") == 0 ||
                               strcmp(comando, "TURN_RIGHT") == 0;
    if (comandoDeMovimento && _safety.pararAtivo()) {
      return false;
    }

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

    if (strcmp(comando, "RADAR_SET_ANGLE") == 0) {
      // Comando de calibracao (fora da lista original do Cap 10 s.5) -
      // move so o servo do radar pra um angulo especifico, sem varredura.
      // Usado pra achar/conferir o arco de rotacao livre na montagem
      // fisica (achado real: colisao com o suporte da webcam ao lado,
      // vistoria de 2026-07-24).
      float angulo = payload["angulo_graus"] | 90.0f;
      _radar.moverServoTeste(static_cast<uint8_t>(constrain(angulo, 0, 180)));
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

    // Rele do ventilador (ativo em LOW) - o Raspberry decide o limiar de
    // temperatura (Cap 17: config/orion.yaml) e manda so o comando ja
    // resolvido; o Arduino nunca decide isso sozinho (Cap 10: so comandos
    // simples, nada de logica/decisao aqui).
    if (strcmp(comando, "FAN_ON") == 0) {
      digitalWrite(pinos::RELE_VENTILADOR, LOW);
      return true;
    }

    if (strcmp(comando, "FAN_OFF") == 0) {
      digitalWrite(pinos::RELE_VENTILADOR, HIGH);
      return true;
    }

    if (strcmp(comando, "SET_PAN_TILT") == 0) {
      // pan_graus/tilt_graus chegam relativos ao centro (-limite..+limite,
      // calculados pelo Vision Core - Cap 8 s.8); o servo fisico espera
      // 0-180 com centro em 90. Reanexa se tiver sido solto por
      // PAN_TILT_SOLTAR (Servo::write() nao reanexa sozinho).
      if (!_servoPan.attached()) _servoPan.attach(pinos::SERVO_PAN);
      if (!_servoTilt.attached()) _servoTilt.attach(pinos::SERVO_TILT);
      float panGraus = payload["pan_graus"] | 0.0f;
      float tiltGraus = payload["tilt_graus"] | 0.0f;
      // SINAL INVERTIDO nos dois eixos (medido na bancada, 2026-07-26): os
      // servos estao montados no sentido oposto ao do contrato. Com "+", o
      // comando pan=+40 ("direita do robo") virava a camera para a ESQUERDA
      // dele, e tilt=+30 ("cima") ABAIXAVA - as duas coisas confirmadas
      // segurando uma posicao por 20s e olhando. Efeito: o seguimento de
      // pessoa FUGIA do alvo em vez de persegui-lo (o Vision Core mandava
      // "desce" e a camera subia).
      //
      // A correcao mora aqui, e nao no pan_tilt.py, porque quem descumpre o
      // contrato do SET_PAN_TILT (positivo = direita/cima) e o firmware -
      // ARQUITETURA.txt regra 3: a logica de controle dos servos fica
      // encapsulada no Arduino. Assim a visao, o testar_pan_tilt.py e
      // qualquer controle manual futuro ficam certos de uma vez so.
      _servoPan.write(constrain(SERVO_CENTRO_GRAUS - panGraus, 0, 180));
      _servoTilt.write(constrain(SERVO_CENTRO_GRAUS - tiltGraus, 0, 180));
      return true;
    }

    if (strcmp(comando, "PAN_TILT_SOLTAR") == 0) {
      // Desliga o torque dos servos pan/tilt (Servo::detach) pra dar pra
      // girar a webcam na mao, sem forcar contra o motor - pedido do
      // usuario durante calibracao fisica, 2026-07-24. SET_PAN_TILT
      // reanexa sozinho na proxima vez que for chamado.
      _servoPan.detach();
      _servoTilt.detach();
      return true;
    }

    return false;  // WHO_ARE_YOU/RETURN_STATUS ja tratados em main.cpp
  }

 private:
  MotorManager& _motores;
  RadarManager& _radar;
  MaquinaDeEstados& _estados;
  SafetyManager& _safety;
  Servo _servoPan;
  Servo _servoTilt;
};

}  // namespace orion
