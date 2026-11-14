"""Os limites da cabeca moram em QUATRO lugares (14/11) e este teste os prende.

O comentario da webui e das acoes faladas prometia "um teste que os prende";
ele nao existia. Quando o topo do tilt desceu de 45 para 30 (o servo tremia),
o joystick da webui ficou em 45 sem ninguem saber. Um numero repetido so e
aceitavel se alguem confere.

- config/orion.yaml `vision.*_limits_degrees` - a visao do Notebook;
- `acoes_faladas` - "olhe para cima" e companhia;
- `WebUIServer` - o servidor corta o que chega do painel;
- `camera_cabeca.js` - o joystick da cabeca no painel.
"""
import re
from pathlib import Path

import yaml

from motion_core.webui.server import WebUIServer
from orion.mission import acoes_faladas

RAIZ = Path(__file__).resolve().parents[2]


def _yaml():
    conf = yaml.safe_load((RAIZ / "config/orion.yaml").read_text(encoding="utf-8"))["vision"]
    return tuple(conf["pan_limits_degrees"]), tuple(conf["tilt_limits_degrees"])


def _js():
    js = (RAIZ / "motion_core/webui/static/camera_cabeca.js").read_text(encoding="utf-8")
    pan_max = float(re.search(r"const PAN_MAX = (-?\d+)", js).group(1))
    tilt = re.search(r"const TILT_MIN = (-?\d+), TILT_MAX = (-?\d+)", js)
    return pan_max, (float(tilt.group(1)), float(tilt.group(2)))


def test_tilt_e_o_mesmo_nos_quatro_lugares():
    _, tilt_yaml = _yaml()
    _, tilt_js = _js()
    esperado = tuple(float(v) for v in tilt_yaml)
    assert (acoes_faladas.TILT_MIN, acoes_faladas.TILT_MAX) == esperado
    assert (WebUIServer.TILT_MIN, WebUIServer.TILT_MAX) == esperado
    assert tilt_js == esperado


def test_pan_e_o_mesmo_nos_quatro_lugares():
    pan_yaml, _ = _yaml()
    pan_max_js, _ = _js()
    esperado = tuple(float(v) for v in pan_yaml)
    assert (acoes_faladas.PAN_MIN, acoes_faladas.PAN_MAX) == esperado
    assert (WebUIServer.PAN_MIN, WebUIServer.PAN_MAX) == esperado
    assert (-pan_max_js, pan_max_js) == esperado
