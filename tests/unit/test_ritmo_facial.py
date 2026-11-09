"""O reconhecimento facial tem ritmo proprio, o YOLO nao (Cap 8 s.4).

Nasceu em 2026-07-30 de uma medicao: o pipeline gastava 567-716 ms por quadro
contra um orcamento de 66,7 ms (`fps: 15`), e o freio de
`_dormir_o_resto_do_quadro` caia em `sleep(0)` sem nunca dormir - um nucleo
inteiro dos 4 do Notebook em 99,2% de CPU, 24 h por dia.

O reconhecimento facial era 78% desse custo (442-716 ms contra 125 ms do YOLO),
e as duas formas obvias de baratea-lo foram MEDIDAS e reprovadas: baixar
upsample/resolucao perde o rosto (0 rostos onde antes achava 1), e usar o YOLO
como porteiro perderia rosto em 15 das 34 fotos reais com rosto (close, sem
corpo a mostra).

Entao o que muda aqui e o RITMO, nao a qualidade. O que estes testes seguram:
que o YOLO nao desacelerou junto, e que a identidade nao pisca entre um
reconhecimento e o proximo.
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
    """Quadros com ruido de sensor, um diferente a cada leitura.

    Eram quadros PRETOS e iguais. Desde 2026-11-01 o Vision Core detecta a
    camera cega (escura e uniforme, ou congelada) e para de rodar o YOLO -
    e um quadro preto repetido e exatamente isso. Camera viva tem ruido.
    """

    def __init__(self):
        self.aberta = True
        self._gerador = np.random.default_rng(0)

    async def abrir(self):
        self.aberta = True

    async def ler_frame(self):
        return self._gerador.integers(40, 200, size=(480, 640, 3), dtype=np.uint8)

    async def fechar(self):
        self.aberta = False


class DetectorContador:
    """Conta chamadas: o YOLO tem que continuar rodando em TODO quadro."""

    def __init__(self, deteccoes):
        self._deteccoes = deteccoes
        self.chamadas = 0

    async def detectar(self, frame):
        self.chamadas += 1
        return self._deteccoes

    @staticmethod
    def pessoas(deteccoes):
        return [d for d in deteccoes if d.classe == "person"]


class ReconhecedorContador:
    def __init__(self, rostos):
        self._rostos = rostos
        self.chamadas = 0

    async def reconhecer(self, frame_rgb):
        self.chamadas += 1
        return self._rostos


def _montar(reconhecer_a_cada, rostos, event_bus):
    pessoa = Deteccao(classe="person", confianca=0.9, caixa=(100, 100, 300, 400))
    detector = DetectorContador([pessoa])
    reconhecedor = ReconhecedorContador(rostos)
    vision = VisionCore(
        event_bus,
        captura=CapturaFalsa(),
        detector=detector,
        reconhecedor=reconhecedor,
        frames_confirmacao_alvo=1,
        reconhecer_rosto_a_cada=reconhecer_a_cada,
    )
    return vision, detector, reconhecedor


@pytest.mark.asyncio
async def test_yolo_todo_quadro_e_rosto_a_cada_n():
    """O ganho tem que vir SO do reconhecimento. Se o YOLO desacelerar junto,
    o robo passa a ver pessoa e obstaculo mais devagar - que e exatamente o
    que esta mudanca promete nao fazer."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    vision, detector, reconhecedor = _montar(3, [], bus)

    for _ in range(9):
        await vision.processar_um_frame()

    assert detector.chamadas == 9, "o YOLO tem que rodar em TODO quadro"
    # quadros 0, 3 e 6 -> 3 reconhecimentos em 9 quadros
    assert reconhecedor.chamadas == 3, (
        f"esperava 3 reconhecimentos em 9 quadros, houve {reconhecedor.chamadas}"
    )

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_o_primeiro_quadro_sempre_reconhece():
    """Comecar a vida sem identidade por N quadros e o pior momento para
    economizar: e justamente quando alguem acabou de aparecer."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    vision, _, reconhecedor = _montar(5, [], bus)

    await vision.processar_um_frame()

    assert reconhecedor.chamadas == 1, "o primeiro quadro nao pode pular o reconhecimento"

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_identidade_nao_pisca_entre_reconhecimentos():
    """REGRESSAO: se os quadros intermediarios devolvessem lista vazia, o alvo
    perderia o nome e o recuperaria a cada dois quadros. O FOLLOW do
    NavigationCore so segue gente RECONHECIDA (vision.person_recognized), entao
    piscar assim faria o robo largar a pessoa no meio do caminho."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    reconhecidos = []
    bus.subscribe("vision.person_recognized", lambda e: reconhecidos.append(e.dados))

    rosto = RostoReconhecido(pessoa_id=1, nome="João Paulo", confianca=0.9, caixa=(150, 120, 250, 260))
    vision, _, _ = _montar(3, [rosto], bus)

    for _ in range(6):
        await vision.processar_um_frame()
    await bus.aguardar_fila_vazia()

    assert len(reconhecidos) == 6, (
        f"a identidade piscou: 6 quadros com a mesma pessoa renderam "
        f"{len(reconhecidos)} eventos de reconhecimento"
    )
    assert all(e["nome"] == "João Paulo" for e in reconhecidos)

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_desconhecido_so_e_anunciado_quando_olhou_de_verdade():
    """A Sentinela salva uma foto por evento. Reaproveitar o rosto e
    republicar o evento faria ela guardar a MESMA cara a cada quadro - havia
    37 fotos acumuladas em data/sentinela antes desta mudanca."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    avisos = []
    bus.subscribe("vision.faces_desconhecidas", lambda e: avisos.append(e.dados))

    estranho = RostoReconhecido(pessoa_id=None, nome=None, confianca=0.0, caixa=(150, 120, 250, 260))
    vision, _, _ = _montar(3, [estranho], bus)

    for _ in range(9):
        await vision.processar_um_frame()
    await bus.aguardar_fila_vazia()

    assert len(avisos) == 3, (
        f"esperava 1 aviso por reconhecimento real (3 em 9 quadros), houve {len(avisos)}"
    )

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_valor_1_mantem_o_comportamento_antigo():
    """Compatibilidade: um orion.yaml sem a chave nova cai no default 1, e o
    modulo tem que se comportar exatamente como antes de 2026-07-30."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    vision, detector, reconhecedor = _montar(1, [], bus)

    for _ in range(5):
        await vision.processar_um_frame()

    assert detector.chamadas == 5
    assert reconhecedor.chamadas == 5, "com 1, o reconhecimento roda em todo quadro"

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_valor_invalido_nao_quebra():
    """0 ou negativo dividiria por zero no `%`. Cai para 1 (todo quadro), que
    e o lado seguro: perde CPU, nunca perde rosto."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    vision, _, reconhecedor = _montar(0, [], bus)

    for _ in range(3):
        await vision.processar_um_frame()

    assert reconhecedor.chamadas == 3

    bus.parar()
    await tarefa
