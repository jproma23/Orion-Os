"""Ponte de sensores do Nav2 (nav2/.../bridge_scan_odom.py), sem ROS instalado.

Ate 2026-11-10 o bridge nao tinha UM teste: o /scan saia com 12 raios no
angulo de INICIO de cada setor (C2-01), um setor sem eco (`None`) derrubava o
timer com TypeError e o mesmo scan era republicado com carimbo "agora" depois
que o LiDAR calava (C2-02). O rclpy e as mensagens sao trocados por stubs
minimos (como na prova da auditoria, scratchpad/provas/c2_scan.py).
"""

import importlib.util
import math
import sys
import types
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_BRIDGE = RAIZ / "nav2" / "src" / "orion_nav2" / "orion_nav2" / "bridge_scan_odom.py"


class _Auto:
    """Mensagem ROS de mentira: qualquer atributo lido vira um _Auto novo."""

    def __getattr__(self, nome):
        if nome.startswith("__"):
            raise AttributeError(nome)
        valor = _Auto()
        object.__setattr__(self, nome, valor)
        return valor


class _Tempo:
    def __init__(self, ns: int) -> None:
        self.ns = ns

    def __sub__(self, duracao):
        return _Tempo(self.ns - duracao.ns)

    def to_msg(self):
        return self.ns


class _Duracao:
    def __init__(self, nanoseconds: int = 0) -> None:
        self.ns = nanoseconds


class _Relogio:
    def now(self):
        return _Tempo(10**12)


class _Logger:
    def __init__(self) -> None:
        self.mensagens: list[tuple[str, str]] = []

    def info(self, m):
        self.mensagens.append(("info", m))

    def warning(self, m):
        self.mensagens.append(("warning", m))

    def error(self, m):
        self.mensagens.append(("error", m))


class _Publicador:
    def __init__(self) -> None:
        self.publicados: list = []

    def publish(self, msg):
        self.publicados.append(msg)


class _Node:
    def __init__(self, *a, **k) -> None:
        self._logger = _Logger()

    def create_publisher(self, *a, **k):
        return _Publicador()

    def create_timer(self, *a, **k):
        return None

    def get_logger(self):
        return self._logger

    def get_clock(self):
        return _Relogio()


class _Broadcaster:
    def __init__(self, *a, **k) -> None:
        self.enviados: list = []

    def sendTransform(self, t):  # noqa: N802 - nome da API do tf2_ros
        self.enviados.append(t)


@pytest.fixture
def bridge(monkeypatch):
    stubs = {
        nome: types.ModuleType(nome)
        for nome in (
            "rclpy", "rclpy.node", "rclpy.duration", "rclpy.time", "geometry_msgs",
            "geometry_msgs.msg", "nav_msgs", "nav_msgs.msg", "sensor_msgs",
            "sensor_msgs.msg", "tf2_ros",
        )
    }
    stubs["rclpy.node"].Node = _Node
    stubs["rclpy.duration"].Duration = _Duracao
    stubs["rclpy.time"].Time = _Tempo  # so o bridge de antes de 10/11 importava
    stubs["geometry_msgs.msg"].Quaternion = _Auto
    stubs["geometry_msgs.msg"].TransformStamped = _Auto
    stubs["nav_msgs.msg"].Odometry = _Auto
    stubs["sensor_msgs.msg"].LaserScan = _Auto
    stubs["tf2_ros"].TransformBroadcaster = _Broadcaster
    for nome, modulo in stubs.items():
        monkeypatch.setitem(sys.modules, nome, modulo)
    spec = importlib.util.spec_from_file_location("bridge_scan_odom_teste", CAMINHO_BRIDGE)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _ponte(bridge, estados):
    """Ponte cujo `_estado()` devolve os estados da lista, um por ciclo."""
    ponte = bridge.PonteScanOdom()
    fila = list(estados)
    ponte._estado = lambda: fila.pop(0) if fila else None
    return ponte


def _raios(padrao_cm=300.0, **pontos):
    raios = [padrao_cm] * 360
    for grau, cm in pontos.items():
        raios[int(grau.lstrip("g"))] = cm
    return raios


def _estado(raios, ts=1.0, posicao=None):
    estado = {"lidar": {"raios_cm": raios, "timestamp_s": ts, "setores": []}}
    if posicao is not None:
        estado["posicao"] = posicao
    return estado


def _angulo_ros_graus(scan, i):
    return math.degrees(scan.angle_min + i * scan.angle_increment)


def test_scan_tem_360_raios_um_por_grau(bridge):
    scan = _ponte(bridge, [])._montar_scan(_estado(_raios()))
    assert len(scan.ranges) == 360
    assert math.degrees(scan.angle_increment) == pytest.approx(1.0)
    assert math.degrees(scan.angle_min) == pytest.approx(-180.0)
    assert math.degrees(scan.angle_max) == pytest.approx(179.0)


def test_ponto_a_115_orion_aparece_a_menos_25_ros(bridge):
    """C2-01: 25 graus a DIREITA da frente; o bridge antigo punha na frente."""
    scan = _ponte(bridge, [])._montar_scan(_estado(_raios(g115=100.0)))
    perto = [i for i, r in enumerate(scan.ranges) if r < 2.0]
    assert len(perto) == 1
    assert _angulo_ros_graus(scan, perto[0]) == pytest.approx(-25.0, abs=1.0)
    assert scan.ranges[perto[0]] == pytest.approx(1.0)


def test_ponto_da_esquerda_fica_em_mais_90_ros(bridge):
    scan = _ponte(bridge, [])._montar_scan(_estado(_raios(g0=50.0)))
    i = scan.ranges.index(0.5)
    assert _angulo_ros_graus(scan, i) == pytest.approx(90.0, abs=1.0)


def test_scan_sai_no_frame_laser(bridge):
    """C2-05: o LiDAR nao esta no base_link; a TF base_link->laser e do launch."""
    scan = _ponte(bridge, [])._montar_scan(_estado(_raios()))
    assert scan.header.frame_id == "laser"


def test_raio_none_vira_inf_e_nao_lanca(bridge):
    """Sem eco = None de proposito desde 19/09; float(None) matava o timer."""
    raios = _raios(g90=None, g10="lixo", g200=float("nan"))
    scan = _ponte(bridge, [])._montar_scan(_estado(raios))
    assert scan.ranges[180] == math.inf  # ROS 0 = ORION 90
    assert sum(math.isinf(r) for r in scan.ranges) == 3


def test_ciclo_com_setor_none_e_pose_quebrada_nao_lanca(bridge):
    estado = _estado(_raios(g90=None))
    estado["lidar"]["setores"] = [{"inicio_graus": 0, "distancia_cm": None}]
    estado["posicao"] = {"x_m": None, "y_m": 0.0, "orientacao_graus": 0.0}
    ponte = _ponte(bridge, [estado])
    ponte._ciclo()  # nao pode lancar
    assert len(ponte._pub_scan.publicados) == 1
    assert ponte._pub_odom.publicados == []  # pose incompleta nao vira 0,0


def test_excecao_inesperada_no_ciclo_nao_derruba_o_timer(bridge):
    ponte = _ponte(bridge, [])

    def explode():
        raise RuntimeError("qualquer coisa")

    ponte._estado = explode
    ponte._ciclo()
    assert any(nivel == "error" for nivel, _ in ponte.get_logger().mensagens)


def test_payload_repetido_nao_e_republicado(bridge):
    """C2-02: mesmo `timestamp_s` = mesmo scan; republicar = mundo congelado."""
    e1 = _estado(_raios(), ts=5.00)
    e2 = _estado(_raios(), ts=5.40)
    ponte = _ponte(bridge, [e1, e1, e1, e2, e2])
    for _ in range(5):
        ponte._ciclo()
    assert len(ponte._pub_scan.publicados) == 2


def test_scan_parado_mais_de_0_6_s_avisa_uma_vez(bridge, monkeypatch):
    relogio = [100.0]
    monkeypatch.setattr(bridge.time, "monotonic", lambda: relogio[0])
    e = _estado(_raios(), ts=5.0)
    ponte = _ponte(bridge, [e] * 10)
    for _ in range(10):
        ponte._ciclo()
        relogio[0] += 0.1
    avisos = [m for nivel, m in ponte.get_logger().mensagens if nivel == "warning"]
    assert len(ponte._pub_scan.publicados) == 1
    assert len([m for m in avisos if "calado" in m]) == 1


def test_carimbo_desconta_a_idade_quando_o_relogio_bate(bridge, monkeypatch):
    """C2-06 (parcial): mesmo host = mesmo CLOCK_MONOTONIC; idade 0,3 s."""
    monkeypatch.setattr(bridge.time, "monotonic", lambda: 50.3)
    scan = _ponte(bridge, [])._montar_scan(_estado(_raios(), ts=50.0))
    assert scan.header.stamp == pytest.approx(10**12 - 0.3e9, abs=1e6)


def test_relogio_diferente_carimba_agora(bridge, monkeypatch):
    monkeypatch.setattr(bridge.time, "monotonic", lambda: 9999.0)
    scan = _ponte(bridge, [])._montar_scan(_estado(_raios(), ts=50.0))
    assert scan.header.stamp == 10**12


def test_sem_raios_ou_tamanho_errado_nao_publica(bridge):
    ponte = _ponte(bridge, [])
    assert ponte._montar_scan({"lidar": None}) is None
    assert ponte._montar_scan({"lidar": {"raios_cm": [100.0] * 12}}) is None


def test_odom_e_tf_saem_da_pose(bridge):
    pos = {"x_m": 1.5, "y_m": -0.5, "orientacao_graus": 90.0, "velocidade_m_s": 0.05}
    ponte = _ponte(bridge, [_estado(_raios(), posicao=pos)])
    ponte._ciclo()
    (odom,) = ponte._pub_odom.publicados
    assert odom.pose.pose.position.x == 1.5
    assert odom.pose.pose.orientation.z == pytest.approx(math.sin(math.pi / 4))
    assert len(ponte._tf.enviados) == 1


def test_cadeia_do_gerenciador_ao_scan(bridge):
    """Driver real -> LidarManager -> bridge: o objeto a 115 graus do robo."""
    stl50b2 = pytest.importorskip("motion_core.lidar.stl50b2")
    from motion_core.lidar.gerenciador import LidarManager

    def quadro(ini, fim, dist_mm):
        b = bytearray(b"\xaa\x55\x00\x19")
        b += int(ini * 100).to_bytes(2, "little") + int(fim * 100).to_bytes(2, "little")
        b += b"\x00\x00"
        for d in dist_mm:
            b += bytes([200]) + (d * 4).to_bytes(2, "little")
        return bytes(b)

    class BusFalso:
        def subscribe(self, *a):
            pass

        async def publish(self, *a, **k):
            pass

    g = LidarManager(BusFalso(), {"driver": "stl50b2", "setores": 12,
                                  "min_distancia_mm": 50, "angulo_frente_graus": 19})
    alvo_lidar = (115 - (90 - 19)) % 360
    for k in range(14):
        ini = (k * 27) % 360
        fim = (ini + 24) % 360
        angs = stl50b2._interpolar_angulos(ini, fim, 25)
        dist = [1000 if int(a) == alvo_lidar else 3000 for a in angs]
        g._quadros.append(stl50b2.decodificar_quadro(quadro(ini, fim, dist)))
    scan_orion = g._montar_scan()
    scan = _ponte(bridge, [])._montar_scan({"lidar": scan_orion})
    perto = [i for i, r in enumerate(scan.ranges) if r < 2.0]
    assert perto
    assert all(abs(_angulo_ros_graus(scan, i) + 25.0) <= 1.0 for i in perto)
