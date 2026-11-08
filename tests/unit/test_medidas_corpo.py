"""As medidas do corpo existem em DOIS lugares, e este teste os prende.

O firmware (C++, sem acesso ao YAML) e o Motion Core (Python) fazem a mesma
conta de odometria: pulsos por metro e bitola. Ja divergiram - 2546 passos/m
e 0,30 m de um lado, 0,200 m de roda e 0,45 m do outro, nenhum medido - e a
pose saia 24% maior que o andado sem ninguem perceber. Melhor repetido e
preso por teste do que derivado por um caminho que o C++ nao alcanca.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]
MOTOR = (RAIZ / "firmware/hardware_core/include/motor_manager.h").read_text(encoding="utf-8")
CONF = yaml.safe_load((RAIZ / "config/orion.yaml").read_text(encoding="utf-8"))


def _constante(nome: str) -> float:
    casado = re.search(rf"constexpr float {nome} = ([0-9.]+)f;", MOTOR)
    assert casado, f"{nome} sumiu do motor_manager.h"
    return float(casado.group(1))


def test_diametro_da_roda_bate():
    assert _constante("RODA_DIAMETRO_M") == CONF["chassi"]["roda_diametro_m"]


def test_bitola_bate():
    assert _constante("BITOLA_M") == CONF["motion"]["wheel_base_m"]


def test_pulsos_por_metro_batem():
    por_volta = _constante("ENCODER_PPR_MOTOR") * 2 * _constante("REDUCAO_ENGRENAGEM")
    firmware = por_volta / (math.pi * _constante("RODA_DIAMETRO_M"))
    assert CONF["motion"]["steps_per_meter"] == round(firmware)
