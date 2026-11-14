"""Exploração autônoma por FRONTEIRAS: o robô vai sozinho até onde o mapa acaba.

Pedido do dono (2026-10-31): "quero que ele se mova pela casa toda desviando
sozinho dos obstáculos e realinhando suas rodas durante o andar".

Como funciona, em laço:
1. lê o /map do SLAM e acha as FRONTEIRAS - células livres encostadas em
   células desconhecidas: é onde a casa ainda não foi vista;
2. junta as fronteiras em grupos e escolhe o grupo mais perto que valha a pena
   (pequeno demais é ruído; perto demais é onde ele já está);
3. pede ao Nav2 (NavigateToPose) para ir até lá - quem desvia de obstáculo e
   corrige o rumo a cada 100 ms é o Nav2 com o LiDAR ao vivo, não este nó;
4. chegou, falhou ou estourou o tempo: marca o alvo e volta ao passo 1.
Acaba quando não sobra fronteira (a casa toda vista) ou no teto de alvos.

PARAR: criar o arquivo /opt/orion_nav2/mapas/PARAR (no Pi:
data/mapas/PARAR). O nó cancela o alvo em curso e sai. O mapa é salvo em
data/mapas/ a cada alvo e no fim.

Roda dentro do container do Nav2 (rclpy + numpy de lá):
    docker exec -d nav2-nav2-1 bash -c "source /opt/ros/humble/setup.bash &&
        python3 /opt/orion_nav2/src/orion_nav2/orion_nav2/explorar.py"
"""

import math
import os
import subprocess
import time

import numpy as np
import rclpy

# Mesmo diretorio: `python3 .../explorar.py` poe a pasta no sys.path.
from explorar_escolha import escolher  # noqa: E402
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import OccupancyGrid
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from tf2_ros import Buffer, TransformListener

PASTA_MAPAS = "/opt/orion_nav2/mapas"
ARQUIVO_PARAR = os.path.join(PASTA_MAPAS, "PARAR")
NOME_MAPA = "casa"

TEMPO_MAXIMO_ALVO_S = 120.0
MAXIMO_ALVOS = 40


class Explorador(Node):
    def __init__(self) -> None:
        super().__init__("orion_explorador")
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                         reliability=ReliabilityPolicy.RELIABLE)
        self._mapa: OccupancyGrid | None = None
        self.create_subscription(OccupancyGrid, "map", self._ao_mapa, qos)
        self._tf = Buffer()
        self._tf_listener = TransformListener(self._tf, self)
        self._nav = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self._lista_negra: list[tuple[float, float]] = []

    def _ao_mapa(self, msg: OccupancyGrid) -> None:
        self._mapa = msg

    def _girar(self, segundos: float) -> None:
        fim = time.monotonic() + segundos
        while time.monotonic() < fim:
            rclpy.spin_once(self, timeout_sec=0.1)

    def _pose(self) -> tuple[float, float] | None:
        try:
            t = self._tf.lookup_transform("map", "base_link", rclpy.time.Time())
        except Exception:
            return None
        return t.transform.translation.x, t.transform.translation.y

    def _escolher(self, pose):
        """CURIOSIDADE (2026-11-14): a fronteira com mais desconhecido atras -
        ver explorar_escolha.py. Substitui a regra antiga (a mais longe)."""
        if self._mapa is None:
            return None
        m = self._mapa
        g = np.array(m.data, dtype=np.int16).reshape(m.info.height, m.info.width)
        return escolher(
            g, m.info.resolution, m.info.origin.position.x, m.info.origin.position.y,
            pose, self._lista_negra,
        )

    def _salvar_mapa(self) -> None:
        subprocess.run(
            ["ros2", "run", "nav2_map_server", "map_saver_cli", "-f",
             os.path.join(PASTA_MAPAS, NOME_MAPA), "--ros-args",
             "-p", "map_subscribe_transient_local:=true"],
            capture_output=True, timeout=30,
        )

    def _ir(self, pose, x, y) -> str:
        alvo = NavigateToPose.Goal()
        alvo.pose = PoseStamped()
        alvo.pose.header.frame_id = "map"
        alvo.pose.header.stamp = self.get_clock().now().to_msg()
        alvo.pose.pose.position.x, alvo.pose.pose.position.y = x, y
        yaw = math.atan2(y - pose[1], x - pose[0])
        alvo.pose.pose.orientation.z, alvo.pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
        futuro = self._nav.send_goal_async(alvo)
        rclpy.spin_until_future_complete(self, futuro, timeout_sec=10)
        handle = futuro.result()
        if handle is None or not handle.accepted:
            return "recusado"
        resultado = handle.get_result_async()
        inicio = time.monotonic()
        while not resultado.done():
            rclpy.spin_once(self, timeout_sec=0.2)
            if os.path.exists(ARQUIVO_PARAR):
                handle.cancel_goal_async()
                self._girar(1.0)
                return "parado pelo dono"
            if time.monotonic() - inicio > TEMPO_MAXIMO_ALVO_S:
                handle.cancel_goal_async()
                self._girar(1.0)
                return "tempo esgotado"
        return {4: "chegou", 5: "cancelado", 6: "abortado"}.get(resultado.result().status, "status %d" % resultado.result().status)

    def executar(self) -> None:
        self.get_logger().info("aguardando mapa, TF e Nav2...")
        self._nav.wait_for_server(timeout_sec=60)
        inicio = time.monotonic()
        while (self._mapa is None or self._pose() is None) and time.monotonic() - inicio < 60:
            self._girar(0.5)
        for numero in range(1, MAXIMO_ALVOS + 1):
            if os.path.exists(ARQUIVO_PARAR):
                self.get_logger().info("PARAR encontrado - saindo")
                break
            self._girar(1.0)  # deixa o mapa atualizar depois do ultimo movimento
            pose = self._pose()
            escolha = self._escolher(pose) if pose else None
            if escolha is None:
                self.get_logger().info("sem fronteira que valha a viagem: exploracao COMPLETA")
                break
            x, y = escolha["x"], escolha["y"]
            self.get_logger().info(
                "alvo %d: (%.2f, %.2f), %.1f m, %.1f m2 desconhecidos atras, %d grupos"
                % (numero, x, y, escolha["distancia"], escolha["ganho_m2"], escolha["grupos"])
            )
            resultado = self._ir(pose, x, y)
            self.get_logger().info("alvo %d: %s" % (numero, resultado))
            if resultado == "parado pelo dono":
                break
            if resultado != "chegou":
                self._lista_negra.append((x, y))
            self._salvar_mapa()
        self._salvar_mapa()
        self.get_logger().info("mapa salvo em %s/%s.pgm" % (PASTA_MAPAS, NOME_MAPA))


def main() -> None:
    rclpy.init()
    no = Explorador()
    try:
        no.executar()
    finally:
        no.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
