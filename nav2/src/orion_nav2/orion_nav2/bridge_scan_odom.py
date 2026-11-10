"""Ponte de SENSORES: publica /scan e /odom a partir do Motion Core.

O Motion Core (host, webui :8080) conhece a pose estimada da fusao de
sensores (motion.position: encoder + bussola) e os 360 raios do LiDAR
STL-50B2 (`lidar.scan` -> estado.lidar.raios_cm). Este no POLLA `/estado` a
10 Hz e traduz em mensagens ROS:

- /scan   (LaserScan, frame `laser`): os 360 raios, um por grau. Convencao
  ORION: 0° = esquerda, 90° = frente, 180° = direita; ROS: 0 = frente,
  +anti-horario. O frame `laser` fica 10 cm ATRAS do base_link (meio do
  eixo das rodas motrizes, que sao as da FRENTE) e a 45 cm do chao - a TF
  estatica base_link -> laser e publicada pelo nav.launch.py (2026-11-10,
  C2-05). Antes o scan saia em base_link, como se o LiDAR estivesse no eixo.
- /odom   (Odometry, frame odom): x/y da fusao e rumo como yaw
  (simplificacao: ORION ja referencia a pose ao norte).
- TF odom -> base_link, mesma pose do /odom.

Scan VELHO nao e publicado (2026-11-10, C2-02): cada `lidar.scan` traz o
`timestamp_s` (monotonic do Pi). Se o valor nao muda entre polls, o scan e o
mesmo - republica-lo com carimbo "agora" fazia o Nav2 andar num mundo
congelado quando o LiDAR calava.

Nada aqui derruba o timer (2026-11-10): ate esta data um setor sem eco
(`distancia_cm: None`, de proposito desde 19/09) dava TypeError dentro do
timer, o `rclpy.spin` saia e o Nav2 ficava sem /scan, /odom e TF sem
ninguem saber. Hoje o ciclo inteiro roda num try, e o launch tem respawn.

Rodando com network_mode: host, o 127.0.0.1 e o proprio host, e o acesso
local da webui e liberado sem senha - nada de credencial por aqui.
"""

import json
import math
import time
import urllib.request

import rclpy
import tf2_ros
from geometry_msgs.msg import Quaternion, TransformStamped
from nav_msgs.msg import Odometry
from rclpy.duration import Duration
from rclpy.node import Node
from sensor_msgs.msg import LaserScan

URL_ESTADO = "http://127.0.0.1:8080/estado"
INTERVALO_S = 0.1

#: Frame do LiDAR. TEM que bater com o child_frame da TF estatica do
#: nav.launch.py (base_link -> laser).
FRAME_LASER = "laser"

#: Scan com o MESMO `timestamp_s` por mais que isto = LiDAR calado (o
#: gerenciador publica a cada ~0,2-0,4 s com ele vivo). 0,6 s e o pedido do
#: dono para a auditoria de 2026-11-10 (C2-02).
SCAN_VELHO_S = 0.6

#: Mesma faixa do orion.yaml (lidar.min_distancia_mm 50 / max 12000).
RANGE_MIN_M = 0.05
RANGE_MAX_M = 12.0


def yaw_para_quaternion(yaw: float) -> Quaternion:
    q = Quaternion()
    q.z = math.sin(yaw / 2.0)
    q.w = math.cos(yaw / 2.0)
    return q


def raio_em_metros(cm) -> float:
    """Um raio do Motion Core (cm, ou None) em metros para o LaserScan.

    Sem eco (None) ou valor que nao e numero vira inf: "nada ate o alcance".
    O costmap so usa inf para LIMPAR com `inf_is_valid: true` (nav2_params);
    0 seria parede colada no LiDAR.
    """
    try:
        metros = float(cm) / 100.0
    except (TypeError, ValueError):
        return float("inf")
    if math.isnan(metros) or metros <= 0.0:
        return float("inf")
    return max(metros, RANGE_MIN_M)


class PonteScanOdom(Node):
    def __init__(self) -> None:
        super().__init__("orion_scan_odom")
        self._pub_scan = self.create_publisher(LaserScan, "scan", 10)
        self._pub_odom = self.create_publisher(Odometry, "odom", 10)
        self._tf = tf2_ros.TransformBroadcaster(self)
        #: `timestamp_s` do ultimo scan publicado e QUANDO (monotonic local)
        #: ele foi visto pela primeira vez - para saber se o LiDAR calou.
        self._ultimo_ts_scan: float | None = None
        self._ts_visto_em = 0.0
        self._avisou_velho = False
        self._timer = self.create_timer(INTERVALO_S, self._ciclo)
        self.get_logger().info(f"Ponte /scan e /odom ativa (Motion Core em {URL_ESTADO})")

    def _estado(self) -> dict | None:
        try:
            with urllib.request.urlopen(URL_ESTADO, timeout=1.0) as resp:
                return json.loads(resp.read()).get("estado")
        except Exception as exc:  # rede/JSON: nao derruba o no
            self.get_logger().warning(f"sem /estado do Motion Core: {exc}")
            return None

    def _scan_e_novo(self, lidar: dict) -> bool:
        """True se este payload ainda nao foi publicado (C2-02).

        O payload so tem o monotonic do Pi, entao a comparacao e do VALOR entre
        polls, sem supor relogio compartilhado: repetido = mesmo scan, nunca
        republicado. Repetido por mais de SCAN_VELHO_S = LiDAR calado, avisa
        uma vez. Sem `timestamp_s` nao ha como julgar e o scan passa.
        """
        ts = lidar.get("timestamp_s")
        if ts is None:
            return True
        agora = time.monotonic()
        if ts != self._ultimo_ts_scan:
            self._ultimo_ts_scan = ts
            self._ts_visto_em = agora
            self._avisou_velho = False
            return True
        if agora - self._ts_visto_em > SCAN_VELHO_S and not self._avisou_velho:
            self._avisou_velho = True
            self.get_logger().warning(
                f"LiDAR calado: o mesmo scan ha {agora - self._ts_visto_em:.1f} s - "
                "/scan suspenso ate chegar um novo"
            )
        return False

    def _idade_scan_s(self, lidar: dict) -> float:
        """Idade do scan, quando o relogio do payload e o daqui coincidem.

        O container roda no MESMO host com o mesmo CLOCK_MONOTONIC, entao
        `time.monotonic() - timestamp_s` e a idade real (C2-06, parcial: antes
        o carimbo era a hora do poll, ate 0,1 s depois da medida). Valor fora
        de [0, SCAN_VELHO_S] significa relogios diferentes - ai carimba agora.
        """
        try:
            idade = time.monotonic() - float(lidar.get("timestamp_s"))
        except (TypeError, ValueError):
            return 0.0
        return idade if 0.0 <= idade <= SCAN_VELHO_S else 0.0

    def _montar_scan(self, estado: dict) -> LaserScan | None:
        """Os 360 raios do LiDAR (2026-10-31), um por grau.

        Ate 31/10 iam os 12 SETORES de 30 graus, no angulo de INICIO de cada
        setor - com 12 raios o SLAM nao tem forma de parede para casar, e um
        objeto 25 graus a direita aparecia na frente (C2-01). O Motion Core
        publica `raios_cm` ja no referencial do ROBO (montagem corrigida pelo
        `angulo_frente_graus`, distancia em cm de verdade - o STL-50B2 manda
        quartos de mm).

        Convencao ORION: 0 = esquerda, 90 = frente, 180 = direita (horario).
        ROS: 0 = frente, positivo para a ESQUERDA (anti-horario). Entao o
        raio ROS de angulo t (graus) e o raio ORION de angulo 90 - t.
        """
        lidar = estado.get("lidar") or {}
        raios = lidar.get("raios_cm")
        if not raios or len(raios) != 360:
            return None
        if not self._scan_e_novo(lidar):
            return None
        scan = LaserScan()
        scan.header.stamp = self._carimbo(self._idade_scan_s(lidar))
        scan.header.frame_id = FRAME_LASER
        scan.angle_min = math.radians(-180.0)
        scan.angle_max = math.radians(179.0)
        scan.angle_increment = math.radians(1.0)
        scan.scan_time = 0.4
        scan.range_min = RANGE_MIN_M
        scan.range_max = RANGE_MAX_M
        scan.ranges = [raio_em_metros(raios[(90 - t) % 360]) for t in range(-180, 180)]
        return scan

    def _montar_odom(self, estado: dict) -> Odometry | None:
        pos = estado.get("posicao")
        if not pos:
            return None
        try:
            x = float(pos["x_m"])
            y = float(pos["y_m"])
            yaw = math.radians(float(pos["orientacao_graus"]))
            v = float(pos.get("velocidade_m_s") or 0.0)
        except (KeyError, TypeError, ValueError):
            # Pose incompleta: melhor nao publicar que publicar 0,0 como se
            # fosse a posicao de verdade.
            return None
        odom = Odometry()
        odom.header.stamp = self._carimbo(0.0)
        odom.header.frame_id = "odom"
        odom.child_frame_id = "base_link"
        odom.pose.pose.position.x = x
        odom.pose.pose.position.y = y
        odom.pose.pose.orientation = yaw_para_quaternion(yaw)
        odom.twist.twist.linear.x = v
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

    def _carimbo(self, idade_s: float):
        agora = self.get_clock().now()
        if idade_s > 0.0:
            agora = agora - Duration(nanoseconds=int(idade_s * 1e9))
        return agora.to_msg()

    def _ciclo(self) -> None:
        # Excecao que escapa daqui mata o rclpy.spin e, com ele, /scan, /odom
        # e a TF. Um ciclo ruim custa um ciclo, nunca o no.
        try:
            estado = self._estado()
            if not estado:
                return
            scan = self._montar_scan(estado)
            if scan is not None:
                self._pub_scan.publish(scan)
            odom = self._montar_odom(estado)
            if odom is not None:
                self._pub_odom.publish(odom)
                self._publicar_tf(odom)
        except Exception as exc:
            self.get_logger().error(f"ciclo da ponte falhou (segue no proximo): {exc!r}")


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
