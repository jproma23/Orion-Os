"""Testes do modulo de visao e do contrato ModuloOrion (EDR-0023).

Usa um nucleo dublê no lugar do VisionCore real: assim o ciclo de vida
(subir, vigiar, desligar) e testado sem camera, sem YOLO e sem
face_recognition instalados.
"""
import asyncio

import pytest

from orion.kernel.event_bus import EventBus
from orion.kernel.modulo import ModuloOrion
from orion.vision.modulo import ModuloVisao, _nome_do_arquivo_do_modelo

CONFIG_VISAO = {
    "resolution": [640, 480],
    "fps": 15,
    "yolo_model": "yolov8n",
    "confidence_threshold": 0.55,
    "pan_limits_degrees": [-80, 80],
    "tilt_limits_degrees": [-30, 45],
    "servo_max_speed_deg_s": 60,
    "camera_indice_principal": 2,
    "camera_frontal_espelhada": True,
}


class NucleoFalso:
    """Imita o VisionCore: um executar() que roda ate parar() ser chamado."""

    def __init__(self, falhar: bool = False) -> None:
        self.falhar = falhar
        self.parou = False
        self.rodou = False

    async def executar(self) -> None:
        self.rodou = True
        if self.falhar:
            raise RuntimeError("camera pegou fogo")
        while not self.parou:
            await asyncio.sleep(0.01)

    def parar(self) -> None:
        self.parou = True


def test_modelo_ganha_sufixo_pt():
    # sem o .pt o Ultralytics trata como nome de catalogo e tenta baixar -
    # quebrando a promessa de funcionar sem internet
    assert _nome_do_arquivo_do_modelo("yolov8n") == "yolov8n.pt"
    assert _nome_do_arquivo_do_modelo("yolov8n.pt") == "yolov8n.pt"


def test_cumpre_o_contrato_de_modulo():
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=NucleoFalso())
    assert isinstance(modulo, ModuloOrion)
    assert modulo.nome == "vision"


@pytest.mark.asyncio
async def test_iniciar_sobe_o_nucleo_e_fica_saudavel():
    nucleo = NucleoFalso()
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=nucleo)

    assert not modulo.esta_saudavel()  # antes de iniciar, nao esta de pe
    await modulo.iniciar()
    await asyncio.sleep(0.05)

    assert nucleo.rodou
    assert modulo.esta_saudavel()
    await modulo.encerrar()


@pytest.mark.asyncio
async def test_encerrar_solta_o_nucleo_e_e_idempotente():
    nucleo = NucleoFalso()
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=nucleo)
    await modulo.iniciar()
    await asyncio.sleep(0.05)

    await modulo.encerrar()
    assert nucleo.parou
    assert not modulo.esta_saudavel()

    # contrato do EDR-0023: encerrar() nunca levanta, mesmo repetido - o
    # desligamento tambem roda depois de falha parcial de boot
    await modulo.encerrar()


@pytest.mark.asyncio
async def test_nucleo_que_morre_deixa_o_modulo_doente():
    """Camera que explode no meio do caminho nao pode passar por saudavel -
    e assim que o Watchdog vai perceber o problema (Cap 6 s.8)."""
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=NucleoFalso(falhar=True))
    await modulo.iniciar()
    await asyncio.sleep(0.05)

    assert not modulo.esta_saudavel()
    await modulo.encerrar()
