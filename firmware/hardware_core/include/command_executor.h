// Command Executor (Cap 10 secao 10) - traduz COMMAND em acao nos managers.
//
// So a logica de despacho; enquadramento/ACK/NACK/RESPONSE continuam em
// main.cpp (ja existentes desde a Fase 2), para nao duplicar
// responsabilidade de protocolo aqui. O que executar() devolve e o que
// decide ACK (reconheceu) ou NACK (comando_desconhecido) - ver main.cpp.
#pragma once

#include <ArduinoJson.h>
// A <Servo.h> do AVR nao existe no ESP32. A ESP32Servo mantem a MESMA API
// (attach/write/detach) e o mesmo nome de classe, entao so o #include muda -
// por baixo ela usa o LEDC (PWM em hardware) em vez de timer compartilhado.
#include <ESP32Servo.h>
#include <string.h>

#include "estado.h"
#include "motor_manager.h"
#include "pins.h"
#include "safety_manager.h"

namespace orion {

// SET_PAN_TILT nao estava na lista original do Cap 10 s.5, mas o hardware
// (servos pan/tilt) esta no Cap 10 s.2 e o Cap 8 s.8 exige o comando para
// centralizar o alvo da visao computacional - extensao justificada, nao
// uma mudanca de arquitetura (mesmo protocolo, mesmo formato de payload).
constexpr float SERVO_CENTRO_GRAUS = 90.0f;

// TETO DO MOVIMENTO COM FIM (2026-11-10, auditoria C1-05/C4-03). LIMITE, nao
// recusa: pedido acima vira o teto - a mesma regra do catalogo falado
// (acoes_faladas.py, "anda 10 metros" anda o maximo). O Python legitimo
// nunca passa disto: voz 200 cm / 360 graus, IA (contrato.py) 150 cm /
// 180 graus, re da webui 15 cm. E a ultima barreira contra um numero
// absurdo (10000 graus = minutos girando) que escape de todas as de cima.
constexpr float DISTANCIA_MAXIMA_CM = 300.0f;
constexpr float GIRO_MAXIMO_GRAUS = 720.0f;

// QUEM RENOVA O HOMEM-MORTO (2026-11-10, auditoria C1-05/C4-03). So comando
// que MEXE ou PARA as rodas. Ficam de fora de proposito: SET_PAN_TILT (a
// visao o manda varias vezes por segundo seguindo uma pessoa - e era ele que
// mantinha vivo para sempre um MOVE_FORWARD sem dono), PAN_TILT_SOLTAR,
// LIGHT_*, WHO_ARE_YOU, RETURN_STATUS. CALIBRATE_COMPASS so conta com
// "girar": true (sem isso as rodas nao mexem). Ver safety_manager.h.
constexpr const char* COMANDOS_DE_MOVIMENTO[] = {
    "MOVE_FORWARD", "MOVE_CONTINUOUS", "MOVE_DISTANCE", "TURN_LEFT", "TURN_RIGHT",
    "CMD_VEL",      "STOP",            "DOCK",          "TEST_WHEEL",
};

inline bool renovaHomemMorto(const char* comando, JsonObjectConst payload) {
  if (strcmp(comando, "CALIBRATE_COMPASS") == 0) return payload["girar"] | false;
  for (const char* nome : COMANDOS_DE_MOVIMENTO) {
    if (strcmp(comando, nome) == 0) return true;
  }
  return false;
}

class CommandExecutor {
 public:
  CommandExecutor(MotorManager& motores, MaquinaDeEstados& estados,
                   SafetyManager& safety)
      : _motores(motores), _estados(estados), _safety(safety) {}

  // Precisa ser chamado de dentro de setup(), nunca no construtor: este
  // objeto e global (criado antes de main()/init() rodar), e Servo::attach()
  // configura um timer de hardware que o init() do Arduino ainda vai
  // reconfigurar em seguida - se attach() rodasse no construtor, o init()
  // pisaria por cima e o servo nunca responderia, mesmo com o codigo
  // "parecendo" correto (mesmo motivo de motores.iniciar()
  // existirem em vez de fazer isso no construtor deles).
  void iniciar() {
    // Rele da lanterna (pins.h, GPIO 17): ATIVO EM LOW, entao o estado
    // seguro e HIGH. Escreve APAGADO antes do pinMode(OUTPUT) - sem isso, o
    // instante em que o pino vira saida o latcharia em LOW, que aqui
    // significa ACENDER.
    digitalWrite(pinos::RELE_LANTERNA, pinos::LANTERNA_APAGADA);
    pinMode(pinos::RELE_LANTERNA, OUTPUT);
    digitalWrite(pinos::RELE_LANTERNA, pinos::LANTERNA_APAGADA);

    // O rele do ventilador SAIU (2026-11-10): o ventilador vai direto na
    // energia e o GPIO 17 e da lanterna agora (pins.h).

    _servoPan.attach(pinos::SERVO_PAN);
    _servoTilt.attach(pinos::SERVO_TILT);
    _servoPan.write(SERVO_CENTRO_GRAUS);
    _servoTilt.write(SERVO_CENTRO_GRAUS);
  }

  // Retorna true se reconheceu e executou o comando; false = comando que
  // este firmware nao conhece, e o main.cpp responde NACK
  // {"motivo": "comando_desconhecido"} em vez de ACK (2026-11-10, C4-01).
  bool executar(const char* comando, JsonObjectConst payload) {
    float velocidade = payload["velocidade_percent"] | 50.0f;

    // Camada reativa (Cap 18 s.9, regra 7 do ARQUITETURA.txt): enquanto uma
    // parada de seguranca estiver ativa (obstaculo ou timeout), nenhum
    // comando de movimento NOVO e aceito, mesmo que venha do Raspberry -
    // so volta a aceitar quando safety.avaliar() confirmar de novo que esta
    // livre. Sem isso, um comando atrasado/reenviado (retry, fila antiga,
    // race condition) religava o motor com o perigo ainda presente (achado
    // real da vistoria de 2026-07-24) - defesa em profundidade junto com o
    // reforco continuo em SafetyManager::avaliar().
    // NENHUM COMANDO E RECUSADO (2026-07-29, EDR-0027).
    //
    // Ate aqui, qualquer comando de movimento era descartado enquanto
    // `_safety.pararAtivo()` fosse verdadeiro. Decisao do mantenedor, dita e
    // reafirmada: *"o mega nao pode vetar, o mega tem informar os dados"*,
    // *"nada tem q bloquear ou impedir de tomar acoes e atitudes"*, *"nao tem
    // freio, existem manobras"*.
    //
    // O perigo continua sendo medido e RELATADO (safety_manager.h continua
    // preenchendo `motivo()` e a telemetria continua carregando distancia,
    // angulo do radar e estado). O que mudou e quem decide o que fazer com
    // isso: agora e o Pi, que tem o mapa e a missao. O Mega mede, informa e
    // executa - nao opina.
    //
    // Consequencia honesta, registrada aqui de proposito: e possivel mandar o
    // robo contra um obstaculo e ele obedecer. A protecao contra isso passou
    // a morar inteiramente na camada de cima.

    if (strcmp(comando, "MOVE_FORWARD") == 0 || strcmp(comando, "MOVE_CONTINUOUS") == 0) {
      _motores.andarFrente(velocidade);
      _estados.transicionarPara(Estado::EXECUTING_MISSION);
      return true;
    }

    if (strcmp(comando, "MOVE_DISTANCE") == 0) {
      float distancia = payload["distancia_cm"] | 0.0f;
      distancia = constrain(distancia, -DISTANCIA_MAXIMA_CM, DISTANCIA_MAXIMA_CM);
      _motores.andarDistancia(distancia, velocidade);
      _estados.transicionarPara(Estado::EXECUTING_MISSION);
      return true;
    }

    if (strcmp(comando, "TURN_LEFT") == 0 || strcmp(comando, "TURN_RIGHT") == 0) {
      bool horario = strcmp(comando, "TURN_RIGHT") == 0;
      if (payload["graus"].is<float>()) {
        float graus = payload["graus"].as<float>();
        graus = constrain(graus, -GIRO_MAXIMO_GRAUS, GIRO_MAXIMO_GRAUS);
        _motores.girarGraus(graus, horario, velocidade);
      } else {
        _motores.girarContinuo(horario, velocidade);
      }
      // O ESTADO NAO MUDA se ainda ha perigo detectado (2026-07-29). Um giro
      // durante um obstaculo e uma MANOBRA de contorno: OBSTACLE_DETECTED
      // continua sendo a verdade, porque a coisa continua la na frente.
      //
      // Anunciar EXECUTING_MISSION aqui faria o Pi concluir que a frente
      // liberou: o comportamento `vigilancia_obstaculo` largaria o controle
      // no meio da propria manobra, e como `avaliar()` so devolve true na
      // BORDA da parada, nada o traria de volta - o estado ficaria preso em
      // EXECUTING_MISSION com o obstaculo ainda ali.
      //
      // Quando a frente limpar de verdade, o main.cpp devolve o estado para
      // IDLE sozinho (perigo encerrado + motores parados).
      if (!_safety.pararAtivo()) {
        _estados.transicionarPara(Estado::EXECUTING_MISSION);
      }
      return true;
    }

    // CMD_VEL (2026-10-31): velocidade continua do Nav2 - v em m/s, w em
    // rad/s (convencao ROS). Chega a 10-20 Hz; o MotorManager para sozinho se
    // ele calar por CMD_VEL_TIMEOUT_MS. Nao usa `velocidade_percent`.
    if (strcmp(comando, "CMD_VEL") == 0) {
      _motores.definirVelocidade(payload["v_m_s"] | 0.0f, payload["w_rad_s"] | 0.0f);
      _estados.transicionarPara(_motores.emMovimento() ? Estado::EXECUTING_MISSION
                                                       : Estado::IDLE);
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

    // SCAN_FRONT e RADAR_SET_ANGLE SAIRAM em 2026-09-19.
    //
    // Os dois moviam o servo do radar e liam o ultrassom frontal; o sensor e
    // o servo foram retirados do robo. SCAN_FRONT era o "estudo de risco" -
    // 7 angulos numa varredura de 2,1 s, com o robo parado. O LiDAR entrega
    // 12 setores de 360 graus a 2,5 Hz, continuamente e sem parar de andar:
    // a varredura nao foi perdida, foi superada.
    //
    // Um comando desconhecido devolve false no fim desta funcao, e o main.cpp
    // responde NACK {"motivo": "comando_desconhecido"} NO LUGAR do ACK. Este
    // comentario prometia isso desde 19/09 e era mentira ate 2026-11-10
    // (C4-01): o main.cpp dava ACK ANTES de saber se o comando existia, e o
    // `varrer_a_frente` da voz dizia "Varrendo a frente" para um SCAN_FRONT
    // que nao fazia nada. Agora o send() do Python recebe o NACK e levanta
    // ErroComunicacao.

    // Lanterna: o pino inverteu em 2026-08-04 porque virou RELE ativo em LOW
    // (ver pins.h). O PROTOCOLO nao mudou - LIGHT_ON/LIGHT_OFF continuam
    // significando acender e apagar, e o Raspberry nao precisa saber de nivel
    // eletrico nenhum. Qual nivel acende e fato de hardware, e mora aqui.
    if (strcmp(comando, "LIGHT_ON") == 0) {
      digitalWrite(pinos::RELE_LANTERNA, pinos::LANTERNA_ACESA);
      return true;
    }

    if (strcmp(comando, "LIGHT_OFF") == 0) {
      digitalWrite(pinos::RELE_LANTERNA, pinos::LANTERNA_APAGADA);
      return true;
    }

    // FAN_ON / FAN_OFF SAIRAM em 2026-11-10 (C4-04). O rele do ventilador
    // estava fisicamente desconectado desde 24/07 e o ventilador vai direto
    // na energia desde 05/08; o pino dele (17) virou a lanterna. Os dois
    // comandos agora caem no NACK (comando_desconhecido) em vez de um ACK
    // para um rele que nao existe.

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

    // Nao reconhecido -> NACK no main.cpp. (WHO_ARE_YOU, RETURN_STATUS,
    // TEST_WHEEL e CALIBRATE_COMPASS nem chegam aqui: o main.cpp os trata.)
    return false;
  }

 private:
  MotorManager& _motores;
  MaquinaDeEstados& _estados;
  SafetyManager& _safety;
  Servo _servoPan;
  Servo _servoTilt;
};

}  // namespace orion
