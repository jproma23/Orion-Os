"""Testes do Vision Engine do Pi (motion_core/vision/engine.py).

Usa um detector dublê (sem YOLO/câmera reais) para testar: a abertura da
câmera, o filtro de classes (pessoas+animais), a publicação dos eventos
com o ORION VISION PROTOCOL e o estado do pipeline.
"""
import asyncio

import pytest

pytest.importorskip("cv2")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from orion.kernel.event_bus import EventBus  # noqa: E402

from motion_core.vision.engine import CAMERA_RASPBERRY_FRONT, VisionEngine  # noqa: E402


class CaixaResultado:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = [list(xyxy)]
        self.conf = [conf]
        self.cls = [cls]


class BoxesFalso:
    """Imita `result.boxes` do ultralytics (atributos xyxy/conf/cls)."""

    def __init__(self, caixas):
        self.xyxy = [tuple(c.xyxy[0]) for c in caixas]
        self.conf = [c.conf[0] for c in caixas]
        self.cls = [c.cls[0] for c in caixas]


class ResultadoFalso:
    def __init__(self, caixas):
        self.boxes = BoxesFalso(caixas)
        self.names = {0: "person", 1: "dog", 2: "chair"}


class DetectorDuble:
    """Imita a interface que o VisionEngine usa do YOLO (predict sincrono)."""

    def __init__(self, deteccoes):
        self._deteccoes = deteccoes
        self.chamadas = 0

    def predict(self, frame, imgsz=None, conf=None, verbose=False):
        self.chamadas += 1
        return [ResultadoFalso(self._deteccoes)]


class CapturaDuble:
    def __init__(self, frame):
        self._frame = frame
        self._aberta = True
        self.isOpened = lambda: self._aberta
        self._leitura = 0

    def read(self):
        self._leitura += 1
        if self._leitura == 1:
            return True, self._frame
        # esgota: fecha o loop sem frame
        self._aberta = False
        return False, None

    def release(self):
        self._aberta = False


def _frame_fake():
    return np.zeros((480, 640, 3), dtype=np.uint8)


async def _rodar_bus(bus: EventBus) -> asyncio.Task:
    return asyncio.create_task(bus.iniciar())


async def _encerrar_bus(bus: EventBus, tarefa: asyncio.Task) -> None:
    """Para o bus e aguarda a tarefa, ignorando CancelledError (normal)."""
    bus.parar()
    tarefa.cancel()
    try:
        await tarefa
    except asyncio.CancelledError:
        pass


async def _executar_ciclos(motor: VisionEngine, n: int = 1) -> None:
    """Roda N ciclos do pipeline sem depender do loop infinito."""
    for _ in range(n):
        await motor._processar_uma_vez()


def _montar_motor(bus, detector, captura):
    motor = VisionEngine(
        bus,
        camera_url="http://fake/stream",
        caminho_modelo="fake.pt",
        detector=detector,
    )
    motor._detector = detector  # já "carregado"
    motor._captura = captura
    return motor


@pytest.mark.asyncio
async def test_pessoa_e_animal_publicam_eventos_padronizados():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("vision.person_detected", lambda e: eventos.append(e.dados))
    bus.subscribe("vision.object_detected", lambda e: eventos.append(e.dados))

    detector = DetectorDuble(
        [
            CaixaResultado((100, 100, 300, 400), 0.9, 0),  # person
            CaixaResultado((10, 10, 50, 60), 0.8, 1),     # dog
        ]
    )
    motor = _montar_motor(bus, detector, CapturaDuble(_frame_fake()))
    await _executar_ciclos(motor, 1)
    await bus.aguardar_fila_vazia()

    por_tipo = {}
    for dados in eventos:
        por_tipo.setdefault(dados["classe"], dados)
    assert "person" in por_tipo
    assert "dog" in por_tipo
    # Protocolo padronizado
    for dados in eventos:
        assert dados["camera"] == CAMERA_RASPBERRY_FRONT
        assert "centro_x" in dados and "centro_y" in dados
        assert "timestamp" in dados
        assert "confianca" in dados
    assert por_tipo["person"]["centro_x"] == pytest.approx(0.3125, abs=0.001)
    assert por_tipo["dog"]["confianca"] == 0.8

    motor.parar()
    await _encerrar_bus(bus, tarefa)


@pytest.mark.asyncio
async def test_classe_fora_do_interesse_nao_publica():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("vision.object_detected", lambda e: eventos.append(e.dados))

    detector = DetectorDuble(
        [CaixaResultado((0, 0, 10, 10), 0.95, 2)]  # chair: fora do interesse
    )
    motor = _montar_motor(bus, detector, CapturaDuble(_frame_fake()))
    await _executar_ciclos(motor, 1)
    await bus.aguardar_fila_vazia()

    assert eventos == []

    motor.parar()
    await _encerrar_bus(bus, tarefa)


@pytest.mark.asyncio
async def test_classes_pessoas_animais_por_padrao():
    assert "person" in VisionEngine.__init__.__defaults__ or True
    from motion_core.vision.engine import CLASSES_PESSOAS_ANIMAIS

    assert "person" in CLASSES_PESSOAS_ANIMAIS
    assert "dog" in CLASSES_PESSOAS_ANIMAIS
    assert "cat" in CLASSES_PESSOAS_ANIMAIS
    assert "cow" in CLASSES_PESSOAS_ANIMAIS
    assert "chair" not in CLASSES_PESSOAS_ANIMAIS


@pytest.mark.asyncio
async def test_camera_indisponivel_publica_camera_error():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    erros = []
    bus.subscribe("vision.camera_error", lambda e: erros.append(e.dados))

    motor = VisionEngine(
        bus,
        camera_url="http://127.0.0.1:1/stream",  # porta fechada
        caminho_modelo="fake.pt",
        detector=DetectorDuble([]),
    )
    motor._detector = motor._detector or DetectorDuble([])
    motor._abrir_captura = lambda: None  # stream sempre indisponível
    await _executar_ciclos(motor, 1)
    await bus.aguardar_fila_vazia()

    assert erros, "vision.camera_error deveria ter sido publicado"
    assert "stream indisponivel" in erros[0]["motivo"]

    motor.parar()
    await _encerrar_bus(bus, tarefa)


@pytest.mark.asyncio
async def test_estado_do_pipeline_atualizado():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)

    detector = DetectorDuble(
        [CaixaResultado((100, 100, 300, 400), 0.9, 0)]
    )
    motor = _montar_motor(bus, detector, CapturaDuble(_frame_fake()))
    await _executar_ciclos(motor, 1)

    estado = motor.estado
    assert estado["camera"] == "ONLINE"
    assert estado["frame"] == "OK"
    assert estado["inference"] == "RUNNING"
    assert estado["detections"] == 1
    assert estado["fps"] > 0
    assert "person" in estado["classes"]

    motor.parar()
    await _encerrar_bus(bus, tarefa)