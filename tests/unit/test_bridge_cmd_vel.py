"""Ponte /cmd_vel do Nav2 (C1-07) - sem ROS: rclpy e Twist sao falsos aqui.

O achado de 09/11: a ponte pegava o volante UMA vez na vida; depois de um
AUTONOMO todo cmd_vel era recusado (409) em silencio e o Nav2 achava que
dirigia. Consertado em 10/11 (episodios), mas a ponte nao tinha teste nenhum.
"""
import sys
import types

import pytest


class _Node:
    def __init__(self, nome):
        self._log = types.SimpleNamespace(
            info=lambda *_: None, warning=lambda *_: None, error=lambda *_: None
        )

    def create_subscription(self, *a, **k):
        return None

    def create_timer(self, *a, **k):
        return None

    def get_logger(self):
        return self._log


@pytest.fixture
def ponte(monkeypatch):
    rclpy = types.ModuleType("rclpy")
    rclpy_node = types.ModuleType("rclpy.node")
    rclpy_node.Node = _Node
    rclpy.node = rclpy_node
    geometry = types.ModuleType("geometry_msgs")
    geometry_msg = types.ModuleType("geometry_msgs.msg")
    geometry_msg.Twist = object
    geometry.msg = geometry_msg
    for nome, modulo in {
        "rclpy": rclpy,
        "rclpy.node": rclpy_node,
        "geometry_msgs": geometry,
        "geometry_msgs.msg": geometry_msg,
    }.items():
        monkeypatch.setitem(sys.modules, nome, modulo)
    monkeypatch.syspath_prepend("nav2/src/orion_nav2")
    sys.modules.pop("orion_nav2.bridge_cmd_vel", None)
    from orion_nav2 import bridge_cmd_vel as modulo

    chamadas = []
    respostas = {"/controle": (200, {"ok": True}), "/velocidade": (200, {"ok": True})}

    def post(caminho, payload, base=modulo.URL):
        chamadas.append(caminho)
        return respostas.get(caminho, (200, {"ok": True}))

    monkeypatch.setattr(modulo, "_post", post)
    relogio = {"t": 100.0}
    monkeypatch.setattr(modulo.time, "monotonic", lambda: relogio["t"])
    no = modulo.PonteCmdVel()

    def twist(v):
        return types.SimpleNamespace(
            linear=types.SimpleNamespace(x=v), angular=types.SimpleNamespace(z=0.0)
        )

    def chega(v):
        no._ao_cmd_vel(twist(v))
        no._ciclo()

    def pausa(s):
        relogio["t"] += s
        no._ciclo()

    return types.SimpleNamespace(
        no=no, chamadas=chamadas, respostas=respostas, chega=chega, pausa=pausa
    )


def test_pede_o_volante_uma_vez_por_episodio(ponte):
    ponte.chega(0.1)
    ponte.chega(0.1)
    assert ponte.chamadas.count("/controle") == 1
    assert ponte.chamadas.count("/velocidade") == 2


def test_409_cancela_o_goal_e_nao_toma_o_volante_de_volta(ponte):
    ponte.chega(0.1)
    ponte.respostas["/velocidade"] = (409, {"erro": "volante com a IA"})
    ponte.chega(0.1)
    assert "/cancelar" in ponte.chamadas
    antes = len(ponte.chamadas)
    ponte.chega(0.1)  # o Nav2 ainda manda: a ponte fica quieta
    assert len(ponte.chamadas) == antes


def test_goal_novo_depois_do_409_pede_o_volante_de_novo(ponte):
    # O bug de 09/11: depois de um AUTONOMO, nunca mais andava.
    ponte.chega(0.1)
    ponte.respostas["/velocidade"] = (409, {})
    ponte.chega(0.1)
    ponte.pausa(1.0)  # cmd_vel sumiu: fim do episodio
    ponte.respostas["/velocidade"] = (200, {"ok": True})
    ponte.chega(0.1)  # goal novo
    assert ponte.chamadas.count("/controle") == 2
    assert ponte.chamadas[-1] == "/velocidade"
