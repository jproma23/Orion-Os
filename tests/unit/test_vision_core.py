"""Testes do orquestrador Vision Core (Cap 8) - logica de associacao de
identidade, eventos e integracao com o rastreamento. Usa componentes
injetados (fakes) no lugar da camera/YOLO/reconhecimento facial de
verdade, mas o modulo em si importa cv2/ultralytics/face_recognition -
pula onde essas libs nao estao instaladas (ex.: neste Raspberry Pi).
"""
import asyncio

import pytest

pytest.importorskip("numpy")
pytest.importorskip("cv2")
pytest.importorskip("ultralytics")
pytest.importorskip("face_recognition")

import numpy as np  # noqa: E402

from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.vision.deteccao import Deteccao  # noqa: E402
from orion.vision.reconhecimento_facial import RostoReconhecido  # noqa: E402
from orion.vision.vision_core import VisionCore  # noqa: E402


class CapturaFalsa:
    def __init__(self, frames):
        self._frames = list(frames)
        self.aberta = True

    async def abrir(self):
        self.aberta = True

    async def ler_frame(self):
        return self._frames.pop(0)

    async def fechar(self):
        self.aberta = False


class DetectorFalso:
    def __init__(self, deteccoes):
        self._deteccoes = deteccoes

    async def detectar(self, frame):
        return self._deteccoes

    @staticmethod
    def pessoas(deteccoes):
        return [d for d in deteccoes if d.classe == "person"]


class ReconhecedorFalso:
    def __init__(self, rostos):
        self._rostos = rostos

    async def reconhecer(self, frame_rgb):
        return self._rostos


async def _rodar_bus(bus: EventBus) -> asyncio.Task:
    return asyncio.create_task(bus.iniciar())


def _frame_fake():
    return np.zeros((480, 640, 3), dtype=np.uint8)


@pytest.mark.asyncio
async def test_pessoa_detectada_gera_evento():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("vision.person_detected", lambda e: eventos.append(e.dados))

    deteccao = Deteccao(classe="person", confianca=0.9, caixa=(100, 100, 300, 400))
    vision = VisionCore(
        bus,
        captura=CapturaFalsa([_frame_fake()]),
        detector=DetectorFalso([deteccao]),
        reconhecedor=ReconhecedorFalso([]),
    )

    await vision.processar_um_frame()
    await bus.aguardar_fila_vazia()

    assert len(eventos) == 1
    assert eventos[0]["classe"] == "person"

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_objeto_nao_pessoa_gera_object_detected():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("vision.object_detected", lambda e: eventos.append(e.dados))

    deteccao = Deteccao(classe="chair", confianca=0.8, caixa=(0, 0, 50, 50))
    vision = VisionCore(
        bus,
        captura=CapturaFalsa([_frame_fake()]),
        detector=DetectorFalso([deteccao]),
        reconhecedor=ReconhecedorFalso([]),
    )

    await vision.processar_um_frame()
    await bus.aguardar_fila_vazia()

    assert len(eventos) == 1
    assert eventos[0]["classe"] == "chair"

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_rosto_sobreposto_a_pessoa_gera_person_recognized():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("vision.person_recognized", lambda e: eventos.append(e.dados))

    pessoa = Deteccao(classe="person", confianca=0.9, caixa=(100, 100, 300, 400))
    rosto = RostoReconhecido(pessoa_id=7, nome="Joao", confianca=0.95, caixa=(120, 110, 200, 190))
    vision = VisionCore(
        bus,
        captura=CapturaFalsa([_frame_fake()]),
        detector=DetectorFalso([pessoa]),
        reconhecedor=ReconhecedorFalso([rosto]),
    )

    await vision.processar_um_frame()
    await bus.aguardar_fila_vazia()

    assert len(eventos) == 1
    assert eventos[0]["pessoa_id"] == 7
    assert eventos[0]["nome"] == "Joao"

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_rosto_sem_sobreposicao_nao_gera_reconhecimento():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("vision.person_recognized", lambda e: eventos.append(e.dados))

    pessoa = Deteccao(classe="person", confianca=0.9, caixa=(0, 0, 50, 50))
    rosto_distante = RostoReconhecido(
        pessoa_id=7, nome="Joao", confianca=0.95, caixa=(500, 500, 550, 550)
    )
    vision = VisionCore(
        bus,
        captura=CapturaFalsa([_frame_fake()]),
        detector=DetectorFalso([pessoa]),
        reconhecedor=ReconhecedorFalso([rosto_distante]),
    )

    await vision.processar_um_frame()
    await bus.aguardar_fila_vazia()

    assert eventos == []

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_pan_tilt_e_publicado_quando_ha_alvo():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    chamadas = []

    async def publicar_pan_tilt(pan, tilt):
        chamadas.append((pan, tilt))

    pessoa = Deteccao(classe="person", confianca=0.9, caixa=(500, 100, 640, 400))
    vision = VisionCore(
        bus,
        captura=CapturaFalsa([_frame_fake()]),
        detector=DetectorFalso([pessoa]),
        reconhecedor=ReconhecedorFalso([]),
        publicar_pan_tilt=publicar_pan_tilt,
    )

    await vision.processar_um_frame()

    assert len(chamadas) == 1

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_person_lost_publicado_quando_alvo_desaparece():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("vision.person_lost", lambda e: eventos.append(e.dados))

    pessoa = Deteccao(classe="person", confianca=0.9, caixa=(100, 100, 300, 400))
    detector = DetectorFalso([pessoa])
    # timeout bem curto: o Rastreador tem uma janela de tolerancia antes de
    # declarar o alvo perdido (evita "perda" falsa por 1 frame ruim) - o
    # teste teria que dormir de verdade por >= timeout_perdido_s (2s por
    # padrao) para ver o evento, entao encurtamos so para este teste.
    vision = VisionCore(
        bus,
        captura=CapturaFalsa([_frame_fake(), _frame_fake()]),
        detector=detector,
        reconhecedor=ReconhecedorFalso([]),
        timeout_alvo_perdido_s=0.0,
    )

    await vision.processar_um_frame()
    detector._deteccoes = []  # frame seguinte: ninguem mais detectado
    await vision.processar_um_frame()
    await bus.aguardar_fila_vazia()

    assert len(eventos) == 1

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_camera_error_e_publicado_e_propagado():
    from orion.vision.captura import ErroCamera

    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("vision.camera_error", lambda e: eventos.append(e.dados))

    class CapturaQuebrada:
        aberta = True

        async def ler_frame(self):
            raise ErroCamera("camera desconectada")

    vision = VisionCore(
        bus,
        captura=CapturaQuebrada(),
        detector=DetectorFalso([]),
        reconhecedor=ReconhecedorFalso([]),
    )

    with pytest.raises(ErroCamera):
        await vision.processar_um_frame()
    await bus.aguardar_fila_vazia()

    assert len(eventos) == 1

    bus.parar()
    await tarefa
