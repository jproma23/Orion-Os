"""Launch do Nav2 + ponte ORION.

- slam_toolbox online_async: mapeia o /scan em /map e publica map -> odom
  (sem mapa estatico - o robo nao tem mapa pre-pronto).
- nav2_bringup navigation_launch: so os nos de NAVEGACAO (planner,
  controller, BT navigator, costmaps) - sem AMCL/map_server, porque o
  /map vem do SLAM.
- orion_nav2: os tres nos da ponte (scan+odom, cmd_vel e goal), executados por
  caminho absoluto (sem colcon - ver Dockerfile). Com RESPAWN (2026-11-10):
  ate esta data uma excecao no timer da ponte de sensores matava o processo
  e o Nav2 seguia de pe sem /scan, /odom e TF - o GerenteNav2 so olha se o
  CONTAINER esta rodando, e o container continuava rodando.
- TF estatica base_link -> laser (2026-11-10, C2-05). Decisao do dono: as
  rodas motrizes sao as da FRENTE e o base_link e o meio desse eixo; o LiDAR
  esta 10 cm ATRAS do eixo, no meio da largura, a ~0,45 m do chao. Sem esta
  TF o /scan saia em base_link, como se o LiDAR estivesse em cima do eixo.
"""

import os

from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare

RAIZ_PACOTE = os.path.dirname(os.path.abspath(__file__))

#: Posicao do LiDAR no base_link (m). x negativo = atras do eixo dianteiro.
#: A distancia LiDAR -> eixo motriz vai virar `chassi.lidar_ate_eixo_motriz_m`
#: no orion.yaml; a altura e o `chassi.lidar_altura_m`. O teste
#: tests/unit/test_nav2_footprint.py prende estes numeros ao orion.yaml.
LASER_X_M = -0.10
LASER_Y_M = 0.0
LASER_Z_M = 0.45

#: Espera antes de ressuscitar uma ponte: curta o bastante para o Nav2 nao
#: estourar o transform_tolerance por muito tempo, longa o bastante para nao
#: virar laco quente se a ponte morrer logo ao subir.
RESPAWN_ATRASO_S = 2.0


def generate_launch_description() -> LaunchDescription:
    params_file = LaunchConfiguration("params_file")
    use_sim_time = LaunchConfiguration("use_sim_time", default="false")

    slam = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution(
                [FindPackageShare("slam_toolbox"), "launch", "online_async_launch.py"]
            )
        ),
        launch_arguments={
            "use_sim_time": use_sim_time,
            "slam_params_file": params_file,
        }.items(),
    )

    nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution(
                [FindPackageShare("nav2_bringup"), "launch", "navigation_launch.py"]
            )
        ),
        launch_arguments={
            "params_file": params_file,
            "use_sim_time": use_sim_time,
            "autostart": "true",
        }.items(),
    )

    pontes = [
        ExecuteProcess(
            cmd=["python3", os.path.join(RAIZ_PACOTE, script)],
            output="screen",
            respawn=True,
            respawn_delay=RESPAWN_ATRASO_S,
        )
        for script in ("bridge_scan_odom.py", "bridge_cmd_vel.py", "bridge_goal.py")
    ]

    tf_laser = Node(
        package="tf2_ros",
        executable="static_transform_publisher",
        name="tf_base_link_laser",
        arguments=[
            "--x", str(LASER_X_M), "--y", str(LASER_Y_M), "--z", str(LASER_Z_M),
            "--frame-id", "base_link", "--child-frame-id", "laser",
        ],
        output="screen",
    )

    return LaunchDescription([slam, nav2, tf_laser, *pontes])
