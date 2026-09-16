"""Launch do Nav2 + ponte ORION.

- slam_toolbox online_async: mapeia o /scan em /map e publica map -> odom
  (sem mapa estatico - o robo nao tem mapa pre-pronto).
- nav2_bringup navigation_launch: so os nos de NAVEGACAO (planner,
  controller, BT navigator, costmaps) - sem AMCL/map_server, porque o
  /map vem do SLAM.
- orion_nav2: os tres nos da ponte (scan+odom, cmd_vel e goal), executados por
  caminho absoluto (sem colcon - ver Dockerfile).
"""

import os

from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare

RAIZ_PACOTE = os.path.dirname(os.path.abspath(__file__))


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

    ponte_scan = ExecuteProcess(
        cmd=["python3", os.path.join(RAIZ_PACOTE, "bridge_scan_odom.py")],
        output="screen",
    )
    ponte_cmd = ExecuteProcess(
        cmd=["python3", os.path.join(RAIZ_PACOTE, "bridge_cmd_vel.py")],
        output="screen",
    )
    ponte_goal = ExecuteProcess(
        cmd=["python3", os.path.join(RAIZ_PACOTE, "bridge_goal.py")],
        output="screen",
    )

    return LaunchDescription([slam, nav2, ponte_scan, ponte_cmd, ponte_goal])