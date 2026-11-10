"""Ponte de GOAL: entrega um destino do Motion Core ao Nav2.

O Motion Core decide ONDE ir (BehaviorCore/IA estrategica) e o Nav2 resolve
COMO chegar (mapa, costmap, planejamento, replanejamento e desvio). Este no
faz a traducao entre os dois mundos:

  Motion Core (host, 127.0.0.1:8081/goal) -> bridge_goal -> NavigateToPose

O destino chega por POST HTTP (JSON: x, y, yaw_rad) porque o Motion Core nao
fala ROS - o mesmo motivo das outras duas pontes. Com network_mode: host o
127.0.0.1 e o proprio host, entao o Motion Core alcanca este no como se fosse
local.

## Threading

O rclpy nao e thread-safe: o node, o action client e o executor ficam num
UNICO spinner em thread propria, e os handlers HTTP aguardam o resultado
atraves de `concurrent.futures.Future.result(timeout)` - que e thread-safe.
Um novo POST /goal cancela o goal anterior (o Motion Core pode redecidir);
POST /cancelar aborta o goal em andamento.
"""

import json
import math
import threading
import time
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rclpy
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node

PORTA = 8081
TIMEOUT_ACAO_S = 600.0  # limite do servidor; o Nav2 decide quando desiste
PASSO_POLL_S = 0.2      # intervalo de checagem de conclusao do goal


def _aguardar(futuro: Future, segundos: float) -> bool:
    """Espera `futuro` concluir com timeout, por polling de `.done()`.

    O Future do rclpy NAO aceita timeout em `.result()`, e `.done()` e
    thread-safe (o spinner resolve em thread propria). Polling a cada
    PASSO_POLL_S da um timeout efetivo sem bloquear o spinner."""
    prazo = time.monotonic() + segundos
    while not futuro.done():
        if time.monotonic() >= prazo:
            return False
        time.sleep(PASSO_POLL_S)
    return True


class _Handler(BaseHTTPRequestHandler):
    servidor = None  # setado pelo no, acessivel a cada request

    def _responder(self, status: int, dados: dict) -> None:
        corpo = json.dumps(dados).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_POST(self) -> None:
        tamanho = int(self.headers.get("Content-Length", 0))
        try:
            corpo = json.loads(self.rfile.read(tamanho)) if tamanho else {}
        except Exception:
            self._responder(400, {"erro": "corpo precisa ser JSON"})
            return

        if self.path == "/goal":
            self.servidor.enviar_goal(corpo, self)
        elif self.path == "/cancelar":
            self.servidor.cancelar_goal(corpo, self)
        else:
            self._responder(404, {"erro": f"caminho desconhecido: {self.path}"})

    def log_message(self, *args) -> None:  # silencia o log padrao do http.server
        pass


class PonteGoal(Node):
    def __init__(self) -> None:
        super().__init__("orion_goal")
        self._cliente = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self._lock = threading.Lock()
        self._goal_handle = None

        # Unico executor + spinner: todos os callbacks ROS (goal aceito,
        # resultado) sao resolvidos aqui; as threads do HTTP so esperam.
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self)
        self._spinner = threading.Thread(target=self._executor.spin, daemon=True)
        self._spinner.start()

        self.get_logger().info(f"Ponte /goal ativa em 127.0.0.1:{PORTA}")

    # ---------- servidor HTTP ----------

    def iniciar_http(self) -> threading.Thread:
        _Handler.servidor = self
        servidor = ThreadingHTTPServer(("127.0.0.1", PORTA), _Handler)
        thread = threading.Thread(target=servidor.serve_forever, daemon=True)
        thread.start()
        return thread

    # ---------- acoes do Nav2 ----------

    def _cancelar_atual(self) -> None:
        with self._lock:
            handle = self._goal_handle
            self._goal_handle = None
        if handle is not None:
            try:
                handle.cancel_goal()
                self.get_logger().info("goal anterior cancelado")
            except Exception as exc:  # noqa: BLE001 - nao derruba o handler
                self.get_logger().warning(f"erro ao cancelar goal anterior: {exc}")

    def enviar_goal(self, corpo: dict, handler: "_Handler") -> None:
        try:
            x = float(corpo["x"])
            y = float(corpo["y"])
            yaw = float(corpo.get("yaw_rad", 0.0))
        except (KeyError, TypeError, ValueError):
            handler._responder(400, {"erro": "precisa de x, y numericos (yaw_rad opcional)"})
            return

        objetivo = NavigateToPose.Goal()
        objetivo.pose.header.frame_id = "map"
        objetivo.pose.header.stamp = self.get_clock().now().to_msg()
        objetivo.pose.pose.position.x = x
        objetivo.pose.pose.position.y = y
        # O YAW ERA LIDO E JOGADO FORA (corrigido em 2026-09-19). Estas duas
        # linhas eram `z = 0.0` e `w = 1.0` fixos, ou seja: yaw 0 sempre. O
        # robo chegava no ponto certo virado sempre para o mesmo lado, e
        # ninguem era avisado - a API aceitava `yaw_rad`, a mensagem de erro
        # acima ainda anuncia "(yaw_rad opcional)", e a resposta voltava 200.
        # O valor atravessa a webui (server.py) e o nav2_goal.py inteiros so
        # para morrer aqui, na ultima linha do caminho.
        #
        # Rotacao no plano vira quaternio so no eixo Z: um giro de `yaw` em
        # torno de Z e (x=0, y=0, z=sen(yaw/2), w=cos(yaw/2)). Com yaw=0 isso
        # da (0,0,0,1), que e exatamente o valor fixo de antes - entao quem
        # nao manda `yaw_rad` continua com o comportamento de sempre.
        objetivo.pose.pose.orientation.z = math.sin(yaw / 2.0)
        objetivo.pose.pose.orientation.w = math.cos(yaw / 2.0)

        self.get_logger().info(f"goal pedido: x={x:.2f} y={y:.2f} yaw={yaw:.2f} rad")
        self._cancelar_atual()

        if not self._cliente.wait_for_server(timeout_sec=5.0):
            handler._responder(503, {"resultado": "falhou", "motivo": "bt_navigator indisponivel"})
            return

        futuro = self._cliente.send_goal_async(objetivo)
        if not _aguardar(futuro, 5.0):
            handler._responder(504, {"resultado": "timeout", "motivo": "goal nao aceito a tempo"})
            return
        handle = futuro.result()

        if handle is None or not handle.accepted:
            handler._responder(503, {"resultado": "falhou", "motivo": "goal recusado"})
            return

        with self._lock:
            self._goal_handle = handle
        self.get_logger().info("goal aceito - aguardando resultado")

        resultado_futuro: Future = handle.get_result_async()
        if not _aguardar(resultado_futuro, TIMEOUT_ACAO_S):
            self._cancelar_atual()
            handler._responder(504, {"resultado": "timeout", "motivo": "goal nao terminou a tempo"})
            return
        resultado = resultado_futuro.result()

        with self._lock:
            self._goal_handle = None
        status = resultado.status if resultado is not None else -1
        if status == 4:  # GoalStatus.STATUS_SUCCEEDED
            self.get_logger().info(f"goal COMPLETADO em ({x:.2f}, {y:.2f})")
            handler._responder(200, {"resultado": "completado"})
        else:
            self.get_logger().warning(f"goal NAO completado (status={status})")
            handler._responder(200, {"resultado": "falhou", "status": status})

    def cancelar_goal(self, corpo: dict, handler: "_Handler") -> None:
        self._cancelar_atual()
        handler._responder(200, {"resultado": "cancelado"})


def main(args=None) -> None:
    rclpy.init(args=args)
    node = PonteGoal()
    node.iniciar_http()
    try:
        # o trabalho ROS roda no spinner (thread propria) e o HTTP nas threads
        # do servidor; o loop principal so mantem o processo vivo.
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
