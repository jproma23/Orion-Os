"""Ponte de COMANDO: repassa o /cmd_vel do Nav2 ao Motion Core, contínuo.

REESCRITA em 2026-10-31. A versão anterior era do tempo do motor de passo: o
firmware só falava "frente ATÉ STOP / gira ATÉ STOP", e esta ponte traduzia
(v, w) em liga-desliga - escolhia frente OU giro, nunca curva, e a ré era
MOVE_DISTANCE de 5 cm repetido. O robô andava aos trancos e o controlador do
Nav2 corrigia uma trajetória que o corpo não conseguia seguir.

Hoje o firmware tem CMD_VEL (v em m/s, w em rad/s) com PID de velocidade por
roda a 50 Hz. Esta ponte só repassa o último Twist a 10 Hz pelo
`POST /velocidade` da webui (que publica `navigation.comando` VELOCIDADE ->
NavigationCore -> CMD_VEL). Medido na bancada: pedido 0,014/0,086 m/s nas
rodas, medido 0,014/0,087.

Três paradas, em camadas:
1. cmd_vel some por TIMEOUT_S -> esta ponte manda v = w = 0 uma vez;
2. a ponte morre ou a rede pisca -> o firmware para sozinho em 0,5 s
   (CMD_VEL_TIMEOUT_MS) sem receber nada;
3. roda mandada girar que não conta pulso por 1,5 s -> o firmware para.

Antes do primeiro movimento, entrega o volante ao modo MANUAL via /controle
(o mesmo caminho do botão do dashboard). Com network_mode: host o acesso é
local (127.0.0.1) e a webui libera sem senha.
"""

import json
import time
import urllib.request

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node

URL = "http://127.0.0.1:8080"
PERIODO_S = 0.1          # 10 Hz: o firmware para se ficar 0,5 s sem nada
TIMEOUT_S = 0.5          # cmd_vel mais velho que isto não vale mais
#: Abaixo disto o Twist é "parado" - ruído do controlador não liga motor.
ZONA_MORTA_LINEAR_M_S = 0.005
ZONA_MORTA_ANGULAR_RAD_S = 0.02


def _post(caminho: str, payload: dict) -> dict | None:
    req = urllib.request.Request(
        URL + caminho,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            return json.loads(resp.read())
    except Exception as exc:  # rede/JSON/HTTP de erro: não derruba o nó
        print(f"[orion_cmd_vel] POST {caminho} falhou: {exc}")
        return None


class PonteCmdVel(Node):
    def __init__(self) -> None:
        super().__init__("orion_cmd_vel")
        self._sub = self.create_subscription(Twist, "cmd_vel", self._ao_cmd_vel, 10)
        self._timer = self.create_timer(PERIODO_S, self._ciclo)
        self._cmd_vel: Twist | None = None
        self._ultima_chegada = 0.0
        self._andando = False
        self._volante_entregue = False
        self.get_logger().info(f"Ponte /cmd_vel contínua ativa (Motion Core em {URL})")

    def _ao_cmd_vel(self, msg: Twist) -> None:
        self._cmd_vel = msg
        self._ultima_chegada = time.monotonic()

    def _entregar_volante(self) -> bool:
        if not self._volante_entregue:
            resposta = _post("/controle", {"modo": "manual"})
            self._volante_entregue = bool(resposta and resposta.get("ok"))
            if self._volante_entregue:
                self.get_logger().info("volante entregue ao modo MANUAL para o Nav2")
            else:
                self.get_logger().warning(f"não consegui entregar o volante: {resposta}")
        return self._volante_entregue

    def _ciclo(self) -> None:
        cmd = self._cmd_vel
        if cmd is not None and time.monotonic() - self._ultima_chegada > TIMEOUT_S:
            cmd = self._cmd_vel = None
        v = cmd.linear.x if cmd else 0.0
        w = cmd.angular.z if cmd else 0.0
        parado = abs(v) < ZONA_MORTA_LINEAR_M_S and abs(w) < ZONA_MORTA_ANGULAR_RAD_S

        if parado:
            # Manda o zero UMA vez ao parar; depois fica quieto e deixa o
            # volante livre para o joystick ou para a IA.
            if self._andando:
                _post("/velocidade", {"v_m_s": 0.0, "w_rad_s": 0.0})
                self._andando = False
            return

        # Só pega o volante quando há movimento REAL: subir o container não
        # deve disputar o robô com quem estiver dirigindo.
        if not self._entregar_volante():
            return
        resposta = _post("/velocidade", {"v_m_s": v, "w_rad_s": w})
        if resposta and resposta.get("ok"):
            self._andando = True
        else:
            self.get_logger().warning(f"velocidade recusada: {resposta}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = PonteCmdVel()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        _post("/velocidade", {"v_m_s": 0.0, "w_rad_s": 0.0})
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
