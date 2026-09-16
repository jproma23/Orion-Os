FROM ros:humble-ros-base

# Nav2 e SLAM no mesmo container que a ponte ORION (decisao do journal
# 2026-08-16: Nav2 em Docker porque nao ha binario ROS 2 para Debian trixie).
# Nada de ROS no host: o container so fala HTTP com o Motion Core (127.0.0.1)
# via network_mode: host no compose.
#
# Sem colcon: os nos da ponte sao scripts diretos (executados por caminho
# absoluto no launch) e slam/nav2 vem do apt do ROS - o mecanismo de
# source do colcon provou-se inerte nesta imagem (hooks pythonpath nao
# atualizavam AMENT_PREFIX_PATH).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ros-humble-nav2-bringup \
        ros-humble-slam-toolbox \
        ros-humble-tf2-geometry-msgs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/orion_nav2
COPY src/ src/

ENV PYTHONUNBUFFERED=1
CMD ["bash", "-c", "source /opt/ros/humble/setup.bash && ros2 launch /opt/orion_nav2/src/orion_nav2/orion_nav2/nav.launch.py params_file:=/opt/orion_nav2/src/orion_nav2/config/nav2_params.yaml"]
