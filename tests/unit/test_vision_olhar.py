"""Para onde a camera olha (2026-11-08): mirar o rosto e procurar quando nao
ve. A parte de calculo (vision/olhar.py) nao precisa de camera nem de modelo;
a parte de integracao usa os falsos de test_vision_core."""
import asyncio

import pytest

from orion.vision.olhar import ConfigOlhar, Procura, ponto_de_mira

# --- mira ------------------------------------------------------------------


def test_sem_rosto_mira_o_alto_da_caixa_e_nao_o_meio():
    x, y = ponto_de_mira((100, 100, 300, 500), [], fracao_altura_cabeca=0.15)
    assert x == 200
    assert y == pytest.approx(160)  # 100 + 400 * 0.15, e nao 300 (o meio)


def test_rosto_sobreposto_a_pessoa_vira_a_mira():
    rosto = (180, 110, 220, 160)
    assert ponto_de_mira((100, 100, 300, 500), [rosto], 0.15) == (200, 135)


def test_rosto_de_outra_pessoa_nao_puxa_a_mira():
    rosto_longe = (500, 100, 540, 150)
    x, y = ponto_de_mira((100, 100, 300, 500), [rosto_longe], 0.15)
    assert (x, y) == (200, pytest.approx(160))


def test_so_pernas_no_quadro_mira_na_borda_de_cima():
    """Caixa cortada no topo (so as pernas aparecem): a mira fica no alto do
    quadro, e a correcao ergue a camera."""
    _, y = ponto_de_mira((200, 0, 400, 480), [], 0.15)
    assert y < 480 * 0.25


# --- procura ---------------------------------------------------------------


def test_procura_anda_devagar_e_ergue_a_camera():
    procura = Procura(ConfigOlhar(velocidade_procura_graus_s=15, tilt_procura_graus=15))
    pan, tilt = procura.proximo(0.0, -10.0, intervalo_s=0.5)
    assert pan == pytest.approx(7.5)
    assert tilt == pytest.approx(-2.5)  # sobe no mesmo ritmo, sem pulo


def test_procura_vira_no_limite_e_nunca_passa_dele():
    procura = Procura(ConfigOlhar(pan_procura_graus=60, velocidade_procura_graus_s=15))
    pan, _ = procura.proximo(58.0, 15.0, intervalo_s=1.0)
    assert pan == 60.0
    pan, _ = procura.proximo(pan, 15.0, intervalo_s=1.0)
    assert pan == 45.0  # voltou


def test_config_ignora_chave_desconhecida_e_usa_padroes():
    config = ConfigOlhar.de_dict({"procurar_apos_s": 3, "chave_velha": 1})
    assert config.procurar_apos_s == 3
    assert config.mirar_no_rosto is True
    assert ConfigOlhar.de_dict(None) == ConfigOlhar()


# --- integracao com o VisionCore -------------------------------------------

pytest.importorskip("numpy")
pytest.importorskip("cv2")
pytest.importorskip("ultralytics")
pytest.importorskip("face_recognition")

from test_vision_core import (  # noqa: E402
    CapturaFalsa,
    DetectorFalso,
    ReconhecedorFalso,
    _frame_vivo,
)

from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.vision.deteccao import Deteccao  # noqa: E402
from orion.vision.vision_core import VisionCore  # noqa: E402


async def _montar(deteccoes, quadros, config):
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    servos = []

    async def mover(pan, tilt):
        servos.append((pan, tilt))

    vision = VisionCore(
        bus,
        captura=CapturaFalsa([_frame_vivo(i) for i in range(quadros)]),
        detector=DetectorFalso(deteccoes),
        reconhecedor=ReconhecedorFalso([]),
        publicar_pan_tilt=mover,
        timeout_alvo_perdido_s=0.0,
        olhar=config,
    )
    return bus, tarefa, vision, servos


async def _encerrar(bus, tarefa):
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_pessoa_com_so_as_pernas_ergue_a_camera():
    pernas = Deteccao(classe="person", confianca=0.9, caixa=(250, 0, 390, 480))
    bus, tarefa, vision, servos = await _montar([pernas], 1, ConfigOlhar())
    await vision.processar_um_frame()
    assert servos and servos[-1][1] > 0  # tilt para cima
    await _encerrar(bus, tarefa)


@pytest.mark.asyncio
async def test_sem_ninguem_procura_depois_do_tempo(monkeypatch):
    relogio = [1000.0]
    monkeypatch.setattr("orion.vision.vision_core.time.monotonic", lambda: relogio[0])
    bus, tarefa, vision, servos = await _montar([], 3, ConfigOlhar(procurar_apos_s=6))

    await vision.processar_um_frame()  # comeca a contar
    relogio[0] += 3
    await vision.processar_um_frame()  # 3 s: ainda espera
    assert servos == []
    relogio[0] += 4
    await vision.processar_um_frame()  # 7 s: procura
    assert len(servos) == 1
    await _encerrar(bus, tarefa)


@pytest.mark.asyncio
async def test_em_manual_a_visao_nao_mexe_na_cabeca(monkeypatch):
    relogio = [1000.0]
    monkeypatch.setattr("orion.vision.vision_core.time.monotonic", lambda: relogio[0])
    pessoa = Deteccao(classe="person", confianca=0.9, caixa=(500, 100, 640, 400))
    bus, tarefa, vision, servos = await _montar([pessoa], 3, ConfigOlhar(procurar_apos_s=0))

    await bus.publish("behavior.modo_controle", {"modo": "manual", "anterior": "autonomo"})
    await bus.aguardar_fila_vazia()
    await vision.processar_um_frame()  # pessoa na borda: em autonomo moveria
    vision._detector._deteccoes = []
    relogio[0] += 30
    await vision.processar_um_frame()
    await vision.processar_um_frame()  # e nem procura
    assert servos == []
    await _encerrar(bus, tarefa)


@pytest.mark.asyncio
async def test_procura_cede_a_vez_quando_outro_dono_mexe(monkeypatch):
    relogio = [1000.0]
    monkeypatch.setattr("orion.vision.vision_core.time.monotonic", lambda: relogio[0])
    bus, tarefa, vision, servos = await _montar(
        [], 3, ConfigOlhar(procurar_apos_s=0, ceder_a_outro_dono_s=10)
    )

    await bus.publish("motion.pan_tilt", {"pan": -40, "tilt": 0, "origem": "explorar"})
    await bus.aguardar_fila_vazia()
    await vision.processar_um_frame()
    relogio[0] += 5
    await vision.processar_um_frame()  # 5 s depois do explore: ainda cede
    assert servos == []
    relogio[0] += 6
    await vision.processar_um_frame()  # 11 s: volta a procurar, a partir de -40
    assert len(servos) == 1 and servos[0][0] > -40 - 10
    await _encerrar(bus, tarefa)
