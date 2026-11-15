"""Painel dos sentidos (2026-11-15): o caso da DV20 cega por 13 h sem ninguem saber."""

from __future__ import annotations

from datetime import datetime

import pytest

from orion.mission.grounding import montar_contexto
from orion.mission.mission_planner import MissionPlanner
from orion.mission.sentidos import PainelSentidos

from test_mission_planner import AiManagerFalso


class _Relogio:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _painel(estado):
    relogio = _Relogio()
    painel = PainelSentidos(
        confirmar_s=10.0, relogio=relogio, agora=lambda: datetime(2026, 11, 14, 17, 40)
    )
    painel.registrar("camera", lambda: estado.get("camera"))
    painel.registrar("microfones", lambda: estado.get("mic"))
    return painel, relogio


def test_tudo_bem_diz_que_todos_respondem():
    painel, _ = _painel({})
    painel.avaliar()
    assert "todos respondendo" in painel.bloco()
    assert painel.falhas() == []


def test_piscada_nao_vira_falha():
    estado = {"camera": "a camera nao manda imagem nova"}
    painel, relogio = _painel(estado)
    painel.avaliar()
    relogio.t = 5.0
    painel.avaliar()
    assert painel.falhas() == []


def test_falha_confirmada_diz_desde_quando_e_o_motivo():
    estado = {"camera": "a camera nao manda imagem nova"}
    painel, relogio = _painel(estado)
    painel.avaliar()
    relogio.t = 11.0
    painel.avaliar()

    assert painel.falhas() == [("camera", "a camera nao manda imagem nova", "17:40")]
    bloco = painel.bloco()
    assert "camera: FALHANDO desde as 17:40" in bloco
    assert "microfones" not in bloco  # so o que falha


def test_quando_volta_sai_do_bloco():
    estado = {"camera": "cega"}
    painel, relogio = _painel(estado)
    painel.avaliar()
    relogio.t = 11.0
    painel.avaliar()
    estado.pop("camera")
    painel.avaliar()
    assert painel.falhas() == []


def test_verificador_quebrado_conta_como_falha():
    painel = PainelSentidos(confirmar_s=0.0)

    def explode():
        raise RuntimeError("boom")

    painel.registrar("lidar", explode)
    painel.avaliar()
    assert painel.falhas()[0][0] == "lidar"


def test_bloco_vai_para_o_prompt():
    texto = montar_contexto(sentidos="MEUS SENTIDOS AGORA (autodiagnostico):\n- camera: FALHANDO")
    assert "camera: FALHANDO" in texto


@pytest.mark.asyncio
async def test_planner_poe_os_sentidos_no_contexto():
    estado = {"camera": "cega"}
    painel = PainelSentidos(confirmar_s=0.0)
    painel.registrar("camera", lambda: estado.get("camera"))
    painel.avaliar()

    planner = MissionPlanner(ai_manager=AiManagerFalso())
    planner.registrar_sentidos(painel)
    contexto = await planner._consultar_contexto(None, "como voce esta?")

    assert "camera: FALHANDO" in contexto["sentidos"]


# ------------------------------------------------------------ estado do Pi
from orion.mission.sentidos import LeitorEstadoPi


def _estado(viva=True, valido=True, ts=1.0, bussola=True):
    return {"estado": {
        "mundo": {"telemetria_viva": viva},
        "lidar": {"valido": valido, "timestamp_s": ts},
        "hardware": {"bussola_conectada": bussola},
    }}


def _leitor(resposta):
    relogio = _Relogio()
    leitor = LeitorEstadoPi("http://pi/estado", relogio=relogio, ler=lambda url: resposta["r"])
    return leitor, relogio


def test_pi_saudavel_nao_acusa_nada():
    leitor, _ = _leitor({"r": _estado()})
    leitor.atualizar()
    assert (leitor.corpo(), leitor.lidar(), leitor.bussola()) == (None, None, None)


def test_corpo_sem_telemetria():
    leitor, _ = _leitor({"r": _estado(viva=False)})
    leitor.atualizar()
    assert "ESP32" in leitor.corpo()


def test_lidar_invalido_e_lidar_congelado():
    resposta = {"r": _estado(valido=False)}
    leitor, relogio = _leitor(resposta)
    leitor.atualizar()
    assert "sem leitura" in leitor.lidar()

    resposta["r"] = _estado(ts=5.0)
    leitor.atualizar()
    assert leitor.lidar() is None
    relogio.t = 30.0
    leitor.atualizar()  # mesmo timestamp_s: o LiDAR parou
    assert "parou" in leitor.lidar()


def test_pi_fora_do_ar_aparece_no_corpo_e_nao_duplica_na_bussola():
    def explode(url):
        raise OSError("timed out")

    leitor = LeitorEstadoPi("http://pi/estado", ler=explode)
    leitor.atualizar()
    assert "timed out" in leitor.corpo()
    assert leitor.bussola() is None


def test_bussola_desconectada():
    leitor, _ = _leitor({"r": _estado(bussola=False)})
    leitor.atualizar()
    assert "bussola" in leitor.bussola()
