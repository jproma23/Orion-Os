"""O corpo agora: mapa do LiDAR no prompt e o movimento conferido (2026-10-31)."""
from __future__ import annotations

import asyncio

import pytest

from orion.mission import corpo_agora
from orion.mission.acoes_faladas import ACOES, parametros_do_comando
from orion.mission.corpo_agora import CorpoAgora, distancia_no_lado


def _setores(frente=None, tras=None, esquerda=None, direita=None):
    """12 setores de 30 graus. 0 = esquerda, 90 = frente, 180 = direita, 270 = tras."""
    valores = {0: esquerda, 90: frente, 180: direita, 270: tras}
    setores = []
    for inicio in range(0, 360, 30):
        centro = inicio + 15
        cm = None
        for angulo, valor in valores.items():
            if valor is not None and abs((centro - angulo + 180) % 360 - 180) <= 30:
                cm = valor
        setores.append(
            {"inicio_graus": float(inicio), "fim_graus": float(inicio + 30), "distancia_cm": cm}
        )
    return setores


def _estado(frente=200.0, tras=150.0, movendo=False, rumo=None, calibrada=False):
    return {
        "lidar": {
            "valido": True,
            "setores": _setores(frente=frente, tras=tras, esquerda=90.0, direita=120.0),
        },
        "hardware": {
            "em_movimento": movendo,
            "bussola_conectada": True,
            "bussola_calibrada": calibrada,
            "rumo_valido": rumo is not None,
            "rumo_graus": rumo or 0.0,
        },
    }


def test_distancia_no_lado_usa_a_convencao_do_lidar():
    setores = _setores(frente=80.0, tras=300.0)
    assert distancia_no_lado(setores, 90.0) == 80.0
    assert distancia_no_lado(setores, 270.0) == 300.0
    assert distancia_no_lado([], 90.0) is None


def test_numero_falado_vira_parametro_e_respeita_o_teto():
    frente = ACOES["andar_para_frente"]
    assert parametros_do_comando(frente, "30")["distancia_cm"] == 30.0
    assert parametros_do_comando(frente, "")["distancia_cm"] == 50.0
    assert parametros_do_comando(frente, "1000 cm")["distancia_cm"] == 200.0
    # re: o numero falado e positivo, o firmware quer negativo
    assert parametros_do_comando(ACOES["andar_para_tras"], "20")["distancia_cm"] == -20.0
    assert parametros_do_comando(ACOES["virar_direita"], "45,5")["graus"] == 45.5
    assert parametros_do_comando(ACOES["calibrar_bussola"], "")["girar"] is True
    # luz nao tem numero: nada muda
    assert parametros_do_comando(ACOES["acender_luz"], "30") == {}


def test_texto_traz_o_mapa_e_a_bussola():
    corpo = CorpoAgora(ler=lambda: _estado(frente=80.0))
    texto = asyncio.run(corpo.texto())
    assert "frente 80 cm" in texto
    assert "SEM calibracao" in texto


def test_sem_estado_nao_ha_texto():
    assert asyncio.run(CorpoAgora(ler=lambda: None).texto()) is None


@pytest.fixture
def rapido(monkeypatch):
    async def dormir(_s):
        return None

    monkeypatch.setattr(corpo_agora.asyncio, "sleep", dormir)


def _conferir(leituras, comando, parametros):
    fila = list(leituras)

    def ler():
        return fila.pop(0) if len(fila) > 1 else fila[0]

    corpo = CorpoAgora(ler=ler)
    enviados = []

    async def enviar():
        enviados.append(comando)

    async def rodar():
        await corpo.conferir(comando, parametros, enviar)
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])

    asyncio.run(rodar())
    assert enviados == [comando]
    return asyncio.run(corpo.texto())


def test_andou_de_verdade(rapido):
    texto = _conferir(
        [_estado(frente=200.0), _estado(frente=175.0, movendo=True), _estado(frente=172.0)],
        "MOVE_DISTANCE", {"distancia_cm": 30.0},
    )
    assert "LiDAR mediu 28 cm" in texto


def test_rodas_no_ar_sao_denunciadas(rapido):
    texto = _conferir(
        [_estado(frente=200.0), _estado(frente=200.0, movendo=True), _estado(frente=199.0)],
        "MOVE_DISTANCE", {"distancia_cm": 30.0},
    )
    assert "NAO viu eu sair do lugar" in texto


def test_re_confere_pelo_lado_de_tras(rapido):
    texto = _conferir(
        [_estado(tras=150.0), _estado(tras=130.0)],
        "MOVE_DISTANCE", {"distancia_cm": -20.0},
    )
    assert "para tras" in texto and "mediu 20 cm" in texto


def test_giro_sem_bussola_nao_finge_saber(rapido):
    texto = _conferir([_estado(), _estado()], "TURN_RIGHT", {"graus": 90.0})
    assert "nao sei quanto girei" in texto


def test_giro_com_bussola_mede_os_graus(rapido):
    texto = _conferir(
        [_estado(rumo=350.0), _estado(rumo=80.0)], "TURN_RIGHT", {"graus": 90.0}
    )
    assert "mediu 90 graus para a direita" in texto
