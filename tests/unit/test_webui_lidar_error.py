"""`lidar.error` invalida o ultimo scan no /estado (2026-11-10, C2-02).

Antes o webui guardava o ultimo `lidar.scan` para sempre: o LiDAR calava, a
ponte do Nav2 continuava lendo o mesmo scan no /estado e o republicava como
se fosse de agora.
"""

import pytest

pytest.importorskip("aiohttp")

from motion_core.webui.server import WebUIServer  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402


def _scan():
    return {
        "raios_cm": [100.0] * 360,
        "setores": [{"inicio_graus": 0, "distancia_cm": 100.0}],
        "timestamp_s": 12.0,
    }


def test_lidar_error_apaga_o_scan_do_estado():
    servidor = WebUIServer(EventBus())
    servidor._atualizar_estado("lidar.scan", _scan())
    assert servidor._estado["lidar"]["raios_cm"]

    servidor._atualizar_estado("lidar.error", {"motivo": "lidar_sem_dados"})

    assert servidor._estado["lidar"] is None
    assert servidor._estado["mapa"]["leituras"] == []
    erros = servidor._estado["diagnostico"]["ultimos_erros"]
    assert erros[-1]["topico"] == "lidar.error"


def test_scan_novo_depois_do_erro_volta_a_valer():
    servidor = WebUIServer(EventBus())
    servidor._atualizar_estado("lidar.error", {"motivo": "lidar_sem_dados"})
    servidor._atualizar_estado("lidar.scan", _scan())
    assert servidor._estado["lidar"]["timestamp_s"] == 12.0
