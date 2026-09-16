"""Ponte de SENSORES: publica /scan e /odom a partir do Motion Core.

O Motion Core (host, webui :8080) conhece a pose estimada da fusao de
sensores (motion.position: passos comandados + bussola) e a leitura do
ultrassom frontal (hardware.obstaculo_frente_cm). Este no POLLA
`/estado` a 10 Hz e traduz em mensagens ROS:

- /scan   (LaserScan, frame base_link): os setores do LD06 quando houver
  (`lidar.scan` -> estado.lidar.setores), senao um UNICO raio frontal
  vindo do ultrassom. Convencao ORION do mapa: 0° = esquerda,
  90° = frente, 180° = direita; ROS: 0 = frente, +anti-horario.
- /odom   (Odometry, frame odom): x/y da fusao e rumo da bussola como
  yaw (simplificacao: ORION ja referencia a pose ao norte).
- TF odom -> base_link, mesma pose do /odom.

Rodando com network_mode: host, o 127.0.0.1 e o proprio host, e o acesso
local da webui e liberado sem senha - nada de credencial por aqui.
"""

import json
import math
import time
import urllib.request

import rclpy
from geometry_msgs.msg import Quaternion, TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import LaserScan
import tf2_ros

URL_ESTADO = "http://127.0.0.1:8080/estado"
INTERVALO_S = 0.1

#: Convencao do mapa ORION -> ROS. ORION: 0° = esquerda, 90° = frente
#: (anti-horario). ROS: 0 = frente, +anti-horario (esquerda). Atras deixa
#: a frente em 0 e a esquerda em +90°, que e o que o Nav2 espera.
def orion_para_ros_rad(graus_orion: float) -> float:
    return math.radians(90.0 - graus_orion)


def yaw_para_quaternion(yaw: float) -> Quaternion:
    q = Quaternion()
    q.z = math.sin(yaw / 2.0)
    q.w = math.cos(yaw / 2.0)
    return q


class PonteScanOdom(Node):
    def __init__(self) -> None:
        super().__init__("orion_scan_odom")
        self._pub_scan = self.create_publisher(LaserScan, "scan", 10)
        self._pub_odom = self.create_publisher(Odometry, "odom", 10)
        self._tf = tf2_ros.TransformBroadcaster(self)
        self._ultimo_scan: LaserScan | None = None
        self._ultimo_odom: Odometry | None = None
        self._timer = self.create_timer(INTERVALO_S, self._ciclo)
        self._ultimo_envio = 0.0
        self.get_logger().info(f"Ponte /scan e /odom ativa (Motion Core em {URL_ESTADO})")

    def _estado(self) -> dict | None:
        try:
            with urllib.request.urlopen(URL_ESTADO, timeout=1.0) as resp:
                return json.loads(resp.read()).get("estado")
        except Exception as exc:  # rede/JSON: nao derruba o no
            self.get_logger().warning(f"sem /estado do Motion Core: {exc}")
            return None

    def _montar_scan(self, estado: dict) -> LaserScan | None:
        scan = LaserScan()
        scan.header.stamp = self._agora()
        scan.header.frame_id = "base_link"
        scan.angle_increment = 0.0
        scan.range_min = 0.05
        scan.range_max = 12.0

        setores = (estado.get("lidar") or {}).get("setores")
        if setores:
            # LD06: cada setor vira um raio do scan, em ordem de angulo ROS.
            raios = [
                (orion_para_ros_rad(float(s.get("inicio_graus", 0.0))),
                 float(s.get("distancia_cm", 0.0)) / 100.0)
                for s in setores
            ]
            raios.sort(key=lambda r: r[0])
            if not raios:
                return None
            scan.angle_min = raios[0][0]
            scan.angle_max = raios[-1][0]
            scan.angle_increment = (raios[-1][0] - raios[0][0]) / max(len(raios) - 1, 1)
            scan.ranges = [max(min(d, scan.range_max), scan.range_min) for _, d in raios]
        else:
            # Ultrassom unico, frontal: um raio so, com angulo 0 (frente).
            hardware = estado.get("hardware") or {}
            frente_cm = hardware.get("obstaculo_frente_cm")
            scan.angle_min = 0.0
            scan.angle_max = 0.0
            scan.ranges = [frente_cm / 100.0 if frente_cm else float("inf")]
        return scan

    def _montar_odom(self, estado: dict) -> Odometry | None:
        pos = estado.get("posicao")
        if not pos:
            return None
        odom = Odometry()
        odom.header.stamp = self._agora()
        odom.header.frame_id = "odom"
        odom.child_frame_id = "base_link"
        odom.pose.pose.position.x = float(pos.get("x_m", 0.0))
        odom.pose.pose.position.y = float(pos.get("y_m", 0.0))
        odom.pose.pose.orientation = yaw_para_quaternion(
            math.radians(float(pos.get("orientacao_graus", 0.0)))
        )
        odom.twist.twist.linear.x = float(pos.get("velocidade_m_s", 0.0))
        return odom

    def _publicar_tf(self, odom: Odometry) -> None:
        t = TransformStamped()
        t.header.stamp = odom.header.stamp
        t.header.frame_id = "odom"
        t.child_frame_id = "base_link"
        t.transform.translation.x = odom.pose.pose.position.x
        t.transform.translation.y = odom.pose.pose.position.y
        t.transform.rotation = odom.pose.pose.orientation
        self._tf.sendTransform(t)

    def _agora(self) -> Time:
        agora = self.get_clock().now().to_msg()
        return agora

    def _ciclo(self) -> None:
        estado = self._estado()
        if not estado:
            return
        scan = self._montar_scan(estado)
        odom = self._montar_odom(estado)
        if scan is not None:
            self._pub_scan.publish(scan)
            self._ultimo_scan = scan
        if odom is not None:
            self._pub_odom.publish(odom)
            self._publicar_tf(odom)
            self._ultimo_odom = odom


def main(args=None) -> None:
    rclpy.init(args=args)
    node = PonteScanOdom()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
