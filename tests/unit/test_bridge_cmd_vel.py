"""Ponte de comando do Nav2 (nav2/.../bridge_cmd_vel.py), sem ROS instalado.

C1-07 (auditoria 2026-11-09): a ponte pegava o volante UMA vez e guardava
"ja entreguei" para sempre. Se o volante voltava para a IA, todo /velocidade
dava 409 e a ponte so logava "velocidade recusada" - o Nav2 achava que
dirigia e o robo nao andava. Ajuste do revisor (10/11): repedir a cada 409
tomava o volante de quem escolheu AUTONOMO - a regra e "uma vez por goal".
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO = RAIZ / "nav2" / "src" / "orion_nav2" / "orion_nav2" / "bridge_cmd_vel.py"


class _Logger:
    def info(self, m):
        pass

    def warning(self, m):
        pass


class _Node:
    def __init__(self, *a, **k):
        pass

    def create_subscription(self, *a, **k):
        return None

    def create_timer(self, *a, **k):
        return None

    def get_logger(self):
        return _Logger()


class _Vetor:
    def __init__(self):
        self.x = 0.0
        self.z = 0.0


class _Twist:
    def __init__(self):
        self.linear = _Vetor()
        self.angular = _Vetor()


@pytest.fixture
def modulo(monkeypatch):
    stubs = {n: types.ModuleType(n) for n in
             ("rclpy", "rclpy.node", "geometry_msgs", "geometry_msgs.msg")}
    stubs["rclpy.node"].Node = _Node
    stubs["geometry_msgs.msg"].Twist = _Twist
    for nome, m in stubs.items():
        monkeypatch.setitem(sys.modules, nome, m)
    spec = importlib.util.spec_from_file_location("bridge_cmd_vel_teste", CAMINHO)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _andar(ponte, v=0.05):
    t = _Twist()
    t.linear.x = v
    ponte._ao_cmd_vel(t)
    ponte._ciclo()


def _post_com_volante(chamadas, modo, recusa=409):
    """Webui de mentira: /velocidade recusa com `recusa` sem o volante."""

    def post_falso(caminho, payload, base=None):
        chamadas.append(caminho)
        if caminho == "/controle":
            modo["manual"] = True
            return 200, {"ok": True}
        if caminho == "/cancelar":
            return 200, {"ok": True}
        if not modo["manual"]:
            return recusa, {"erro": "recusado"}
        return 200, {"ok": True}

    return post_falso


@pytest.mark.parametrize("recusa", [409, 423])
def test_recusa_no_meio_do_goal_cancela_e_nao_repede(modulo, monkeypatch, recusa):
    """409 = gente tocou AUTONOMO; 423 = PARAR. Nenhum dos dois e repedido.

    Uma vez por episodio: so depois do cmd_vel sumir (pausa > TIMEOUT_S) e
    voltar - um goal novo - a ponte pede o volante de novo, UMA vez.
    """
    relogio = [100.0]
    monkeypatch.setattr(modulo.time, "monotonic", lambda: relogio[0])
    chamadas = []
    modo = {"manual": False}
    monkeypatch.setattr(modulo, "_post", _post_com_volante(chamadas, modo, recusa))
    ponte = modulo.PonteCmdVel()

    _andar(ponte)
    assert chamadas == ["/controle", "/velocidade"]

    # Alguem toca AUTONOMO (ou PARAR): o volante sai do Nav2.
    modo["manual"] = False
    chamadas.clear()
    _andar(ponte)  # recusa
    assert chamadas == ["/velocidade", "/cancelar"]
    assert chamadas.count("/controle") == 0

    # O Nav2 ainda manda cmd_vel por um tempo: nenhum POST, nada de /controle.
    chamadas.clear()
    for _ in range(5):
        relogio[0] += 0.1
        _andar(ponte)
    assert chamadas == []

    # Pausa maior que TIMEOUT_S (goal acabou) e movimento novo (goal novo).
    relogio[0] += modulo.TIMEOUT_S + 0.1
    ponte._ciclo()
    chamadas.clear()
    for _ in range(3):
        relogio[0] += 0.1
        _andar(ponte)
    assert chamadas.count("/controle") == 1
    assert ponte._andando is True


def test_sem_409_nao_pede_o_volante_a_cada_ciclo(modulo, monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        modulo, "_post", lambda c, p, base=None: (chamadas.append(c), (200, {"ok": True}))[1]
    )
    ponte = modulo.PonteCmdVel()
    for _ in range(3):
        _andar(ponte)
    assert chamadas.count("/controle") == 1


def test_post_devolve_o_status_do_http_error(modulo, monkeypatch):
    import io
    import urllib.error

    def urlopen_falso(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 409, "Conflict", {}, io.BytesIO(b'{"erro": "IA"}')
        )

    monkeypatch.setattr(modulo.urllib.request, "urlopen", urlopen_falso)
    assert modulo._post("/velocidade", {}) == (409, {"erro": "IA"})
