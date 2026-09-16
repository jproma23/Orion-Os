"""Ponte de COMANDO: traduz /cmd_vel do Nav2 em comandos do Motion Core.

O Motion Core nao fala "velocidade continua" - o vocabulario e discreto
(webui/server.py COMANDOS_PERMITIDOS): MOVE_CONTINUOUS, TURN_LEFT/RIGHT
(sao CONTINUOS ate STOP no firmware) e MOVE_DISTANCE (a re e feita em
passos curtos, porque o SafetyManager so olha o sensor frontal).

Entao este no nao replica cmd_vel a 10 Hz: ele detecta a ACAO desejada
(frente/re/turn/stop), manda o comando UMA vez quando ela muda (STOP
antes, para nao empilhar sobre movimento em andamento - o NavigationCore
aguarda ocioso antes de aceitar outro) e manda STOP quando o cmd_vel
zera ou some por mais de TIMEOUT_S. A re vira MOVE_DISTANCE curto e
repetido a cada ciclo (cada pedido anda ~5 cm).

Antes de comandar, entrega o volante ao modo MANUAL via /controle (o
mesmo caminho do botao do dashboard). Com network_mode: host o acesso e
local (127.0.0.1) e a webui libera sem senha.
"""

import json
import math
import time
import urllib.error
import urllib.request

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node

URL = "http://127.0.0.1:8080"
DEADBAND_LINEAR_M_S = 0.03
DEADBAND_ANGULAR_RAD = 0.05
VELOCIDADE_MAX_M_S = 0.3        # cmd_vel max -> 100%
VELOCIDADE_MIN_PERCENT = 20.0   # piso do motor (webui VELOCIDADE_MINIMA_PERCENT)
VELOCIDADE_MAX_PERCENT = 80.0   # teto conservador para autonomia
RE_PASSOS_CM = -5.0             # passo curto da re (RE_MAXIMA_CM = 15)
TIMEOUT_S = 0.5                 # sem cmd_vel nesse tempo -> STOP
PERIODO_S = 0.1

STOP = "stop"
FORWARD = "forward"
REVERSE = "reverse"
LEFT = "left"
RIGHT = "right"


def _post(caminho: str, payload: dict) -> dict | None:
    req = urllib.request.Request(
        URL + caminho,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return json.loads(resp.read())
    except Exception as exc:  # rede/JSON: nao derruba o no
        print(f"[orion_cmd_vel] POST {caminho} falhou: {exc}")
        return None


class PonteCmdVel(Node):
    def __init__(self) -> None:
        super().__init__("orion_cmd_vel")
        self._sub = self.create_subscription(Twist, "cmd_vel", self._ao_cmd_vel, 10)
        self._timer = self.create_timer(PERIODO_S, self._ciclo)
        self._cmd_vel: Twist | None = None
        self._ultima_chegada = 0.0
        self._acao_atual = STOP
        self._volante_entregue = False
        self.get_logger().info(f"Ponte /cmd_vel ativa (Motion Core em {URL})")

    def _ao_cmd_vel(self, msg: Twist) -> None:
        self._cmd_vel = msg
        self._ultima_chegada = time.monotonic()

    def _entregar_volante(self) -> None:
        if self._volante_entregue:
            return
        resposta = _post("/controle", {"modo": "manual"})
        if resposta and resposta.get("ok"):
            self._volante_entregue = True
            self.get_logger().info("volante entregue ao modo MANUAL para o Nav2")
        else:
            self.get_logger().warning(f"nao consegui entregar o volante: {resposta}")
    def _desejada(self) -> str:
        cmd = self._cmd_vel
        if cmd is None:
            return STOP
        linear = cmd.linear.x
        angular = cmd.angular.z
        if abs(linear) < DEADBAND_LINEAR_M_S and abs(angular) < DEADBAND_ANGULAR_RAD:
            return STOP
        # Movimento continuo nao combina re com giro nem frente com giro:
        # so a re e com o giro (MOVE_DISTANCE aceita virar? nao). Prioridade:
        # frente quando o linear domina, giro quando o angular domina.
        if abs(linear) > abs(angular):
            return FORWARD if linear > 0 else REVERSE
        return LEFT if angular > 0 else RIGHT

    def _percentual(self, acao: str) -> float:
        cmd = self._cmd_vel or Twist()
        valor = abs(cmd.linear.x) if acao in (FORWARD, REVERSE) else abs(cmd.angular.z)
        escala = VELOCIDADE_MAX_M_S if acao in (FORWARD, REVERSE) else 1.0
        # giro: 0.05..1.0 rad/s -> percentual proporcional (escala = 1 rad/s)
        pct = min(100.0 * valor / escala, 100.0)
        return max(VELOCIDADE_MIN_PERCENT, min(pct, VELOCIDADE_MAX_PERCENT))

    def _enviar(self, acao: str) -> None:
        payload: dict = {"comando": "STOP"}
        if acao == FORWARD:
            payload = {"comando": "MOVE_CONTINUOUS", "velocidade_percent": self._percentual(acao)}
        elif acao == REVERSE:
            payload = {
                "comando": "MOVE_DISTANCE",
                "velocidade_percent": self._percentual(acao),
                "distancia_cm": RE_PASSOS_CM,
            }
        elif acao == LEFT:
            payload = {"comando": "TURN_LEFT", "velocidade_percent": self._percentual(acao)}
        elif acao == RIGHT:
            payload = {"comando": "TURN_RIGHT", "velocidade_percent": self._percentual(acao)}
        resposta = _post("/comando", payload)
        if resposta and resposta.get("ok"):
            self.get_logger().info(f"comando -> {payload['comando']}")
        else:
            self.get_logger().warning(f"comando recusado: {resposta}")

    def _ciclo(self) -> None:
        # So pega o volante quando ha movimento REAL (cmd_vel nao nulo):
        # subir o container nao deve disputar o robo com quem estiver
        # dirigindo (humano ou outro agente) - a entrega so acontece na
        # primeira ordem de movimento.
        if self._cmd_vel is not None and time.monotonic() - self._ultima_chegada > TIMEOUT_S:
            self._cmd_vel = None
        acao = self._desejada()
        if acao != STOP:
            self._entregar_volante()
        if not self._volante_entregue:
            return
        if acao == self._acao_atual:
            # A re e discreta: repete o passo enquanto o cmd_vel pedir.
            if acao == REVERSE:
                self._enviar(REVERSE)
            return
        if self._acao_atual != STOP:
            self._enviar(STOP)
            self._acao_atual = STOP
        if acao != STOP:
            self._enviar(acao)
        self._acao_atual = acao


def main(args=None) -> None:
    rclpy.init(args=args)
    node = PonteCmdVel()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
