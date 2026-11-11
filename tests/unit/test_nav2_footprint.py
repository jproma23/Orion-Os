"""O corpo que o Nav2 enxerga tem que ser o corpo medido (2026-11-10, C2-05).

O nav2_params usava `robot_radius: 0.20` (depois 0.25) centrado no
base_link - um circulo que cobre 25 cm para tras de um corpo que tem 32 a
partir do eixo. Decisao do dono: rodas motrizes na FRENTE, base_link = meio
desse eixo, LiDAR 10 cm atras do eixo. Este teste prende o footprint e a TF
base_link -> laser do nav.launch.py ao `chassi` do config/orion.yaml.

`chassi.lidar_ate_eixo_motriz_m` (0,10) esta no orion.yaml desde 10/11; o
padrao 0,10 abaixo so cobre um yaml antigo. Medidas confirmadas pelo dono
em 11/11.
"""

import ast  # o launch e lido sem importar: launch/launch_ros nao existem fora do container
import json
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

RAIZ = Path(__file__).resolve().parents[2]
PARAMS = RAIZ / "nav2" / "src" / "orion_nav2" / "config" / "nav2_params.yaml"
LAUNCH = RAIZ / "nav2" / "src" / "orion_nav2" / "orion_nav2" / "nav.launch.py"
ORION = RAIZ / "config" / "orion.yaml"

TOLERANCIA_M = 0.01
LIDAR_ATE_EIXO_PADRAO_M = 0.10


@pytest.fixture(scope="module")
def chassi():
    return yaml.safe_load(ORION.read_text(encoding="utf-8"))["chassi"]


@pytest.fixture(scope="module")
def params():
    return yaml.safe_load(PARAMS.read_text(encoding="utf-8"))


def _costmaps(params):
    return {
        "local": params["local_costmap"]["local_costmap"]["ros__parameters"],
        "global": params["global_costmap"]["global_costmap"]["ros__parameters"],
    }


def _esperado(chassi):
    """Retangulo do corpo no base_link, derivado so do orion.yaml."""
    eixo = float(chassi.get("lidar_ate_eixo_motriz_m", LIDAR_ATE_EIXO_PADRAO_M))
    lidar_x = -eixo
    traseira = lidar_x - (float(chassi["profundidade_m"]) - float(chassi["lidar_recuo_m"]))
    ponta_corpo = lidar_x + float(chassi["lidar_recuo_m"])
    # A roda motriz passa do eixo pelo raio dela: se a ponta do corpo fica
    # atras disso (fica: -0,02 contra +0,03), quem bate primeiro e a roda.
    frente = max(ponta_corpo, float(chassi["roda_diametro_m"]) / 2.0)
    meia_largura = float(chassi["largura_m"]) / 2.0
    return traseira, frente, meia_largura


@pytest.mark.parametrize("nome", ["local", "global"])
def test_footprint_e_o_retangulo_do_corpo(nome, chassi, params):
    costmap = _costmaps(params)[nome]
    assert "robot_radius" not in costmap, "robot_radius e footprint juntos: o Nav2 ignora um"
    pontos = json.loads(costmap["footprint"])
    xs = [p[0] for p in pontos]
    ys = [p[1] for p in pontos]
    traseira, frente, meia_largura = _esperado(chassi)
    assert min(xs) == pytest.approx(traseira, abs=TOLERANCIA_M)
    assert max(xs) == pytest.approx(frente, abs=TOLERANCIA_M)
    assert max(ys) == pytest.approx(meia_largura, abs=TOLERANCIA_M)
    assert min(ys) == pytest.approx(-meia_largura, abs=TOLERANCIA_M)


def test_valores_absolutos_da_decisao_do_dono(params):
    """-0,32 / +0,03 / +-0,195, escritos pelo dono em 10/11."""
    pontos = json.loads(_costmaps(params)["local"]["footprint"])
    assert sorted({p[0] for p in pontos}) == [-0.32, 0.03]
    assert sorted({p[1] for p in pontos}) == [-0.195, 0.195]


def _constantes_do_launch():
    arvore = ast.parse(LAUNCH.read_text(encoding="utf-8"))
    valores = {}
    for no in arvore.body:
        if isinstance(no, ast.Assign) and len(no.targets) == 1:
            alvo = no.targets[0]
            if isinstance(alvo, ast.Name) and alvo.id.startswith("LASER_"):
                valores[alvo.id] = ast.literal_eval(no.value)
    return valores


def test_tf_do_laser_bate_com_o_chassi(chassi):
    c = _constantes_do_launch()
    eixo = float(chassi.get("lidar_ate_eixo_motriz_m", LIDAR_ATE_EIXO_PADRAO_M))
    assert c["LASER_X_M"] == pytest.approx(-eixo, abs=TOLERANCIA_M)
    assert c["LASER_Y_M"] == pytest.approx(0.0, abs=TOLERANCIA_M)
    assert c["LASER_Z_M"] == pytest.approx(float(chassi["lidar_altura_m"]), abs=TOLERANCIA_M)
    texto = LAUNCH.read_text(encoding="utf-8")
    assert '"--child-frame-id", "laser"' in texto


def test_pontes_tem_respawn():
    """Ponte morta = Nav2 sem /scan, /odom e TF com o container de pe."""
    texto = LAUNCH.read_text(encoding="utf-8")
    assert "respawn=True" in texto
    for script in ("bridge_scan_odom.py", "bridge_cmd_vel.py", "bridge_goal.py"):
        assert script in texto


def test_inf_limpa_o_costmap(params):
    """O bridge manda inf para raio sem eco; sem inf_is_valid o layer o joga fora."""
    voxel = _costmaps(params)["local"]["voxel_layer"]
    for fonte in voxel["observation_sources"].split():
        assert voxel[fonte]["inf_is_valid"] is True


def test_sem_observation_sources_solto_no_local_costmap(params):
    local = _costmaps(params)["local"]
    assert "observation_sources" not in local
    assert "scan" not in local


def test_slam_toolbox_usa_os_nomes_que_ele_le(params):
    slam = params["slam_toolbox"]["ros__parameters"]
    assert slam["map_frame"] == "map"
    assert slam["odom_frame"] == "odom"
    assert "map_frame_name" not in slam
    assert "odom_frame_name" not in slam
