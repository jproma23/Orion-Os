"""Auditoria do YOLO/visao de 12/11 - "a visao nao reconhece nada".

Cada teste prova um caminho, no codigo, que termina em "nao estou vendo
nada" (ou em nome nenhum) com a camera funcionando e gente na frente:

1. Cena parada + camera com pouco ruido + MJPEG = "imagem congelada"
   falsa -> o YOLO deixa de rodar.
2. Rosto reconhecido de perto, sem corpo a mostra (15 de 34 fotos reais,
   medido em 07/2026 - ver orion.yaml) -> o nome era jogado fora, porque so
   rosto que casasse com uma caixa "person" do YOLO virava identidade.
3. Um rosto podia dar nome a DUAS pessoas (qualquer sobreposicao > 0, sem
   exclusividade) - o nome grudava em quem estava do lado.
4. Visao cega (sem sinal, pausada, camera fechada) respondia o mesmo
   "nao estou vendo nada" de uma sala vazia.
"""
import asyncio

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.vision.sem_sinal import DetectorSemSinal  # noqa: E402

# ------------------------------------------------ 1. falso "congelado"


def _comodo() -> np.ndarray:
    quadro = np.full((480, 640, 3), 170, np.float32)
    cv2.rectangle(quadro, (100, 200), (250, 400), (60, 80, 90), -1)
    cv2.circle(quadro, (450, 240), 60, (30, 30, 200), -1)
    return quadro


def _mjpeg(quadro: np.ndarray, gerador, ruido: float, qualidade: int = 80) -> np.ndarray:
    com_ruido = np.clip(quadro + gerador.normal(0, ruido, quadro.shape), 0, 255).astype(np.uint8)
    _, jpeg = cv2.imencode(".jpg", com_ruido, [cv2.IMWRITE_JPEG_QUALITY, qualidade])
    return cv2.imdecode(jpeg, cv2.IMREAD_COLOR)


def test_cena_parada_com_pouco_ruido_nao_e_camera_congelada():
    # Medido 12/11: ruido 0,5 + JPEG 80 da diferenca media 0,13 entre
    # quadros - abaixo do antigo limiar 0,2. Camera boa, sala parada, e o
    # robo se declarava cego e parava o YOLO.
    gerador = np.random.default_rng(1)
    detector = DetectorSemSinal()
    resultados = [detector.avaliar(_mjpeg(_comodo(), gerador, 0.5)) for _ in range(12)]
    assert not any(r.sem_sinal for r in resultados)


def test_mesmo_jpeg_reenviado_continua_sendo_congelado():
    # A tela "NO SIGNAL" do ustreamer: o MESMO quadro, byte a byte.
    gerador = np.random.default_rng(2)
    fixo = _mjpeg(_comodo(), gerador, 0.5)
    detector = DetectorSemSinal()
    resultados = [detector.avaliar(fixo.copy()) for _ in range(8)]
    assert resultados[-1].sem_sinal and resultados[-1].motivo == "imagem_congelada"


# ------------------------------------------------ 2 e 3. rostos e nomes

pytest.importorskip("ultralytics")
pytest.importorskip("face_recognition")

from orion.vision.deteccao import Deteccao  # noqa: E402
from orion.vision.reconhecimento_facial import RostoReconhecido  # noqa: E402
from orion.vision.vision_core import VisionCore  # noqa: E402


class _Captura:
    aberta = True

    def __init__(self, quadros: int = 3) -> None:
        self._n = quadros
        self._gerador = np.random.default_rng(3)

    async def ler_frame(self):
        return _mjpeg(_comodo(), self._gerador, 2.0)


class _Detector:
    def __init__(self, deteccoes):
        self.deteccoes = deteccoes

    async def detectar(self, frame):
        return self.deteccoes


class _Reconhecedor:
    def __init__(self, rostos):
        self.rostos = rostos

    async def reconhecer(self, frame_rgb):
        return self.rostos


async def _visao(deteccoes, rostos):
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    eventos: list[dict] = []
    bus.subscribe("vision.person_recognized", lambda e: eventos.append(e.dados))
    visao = VisionCore(
        bus,
        captura=_Captura(),
        detector=_Detector(deteccoes),
        reconhecedor=_Reconhecedor(rostos),
    )
    await visao.processar_um_frame()
    await bus.aguardar_fila_vazia()
    tarefa.cancel()
    return visao, eventos


JOAO_DE_PERTO = RostoReconhecido(
    pessoa_id=1, nome="Joao", confianca=0.6, caixa=(200, 100, 440, 380)
)


@pytest.mark.asyncio
async def test_rosto_de_perto_sem_corpo_e_reconhecido_na_cena():
    visao, _ = await _visao([], [JOAO_DE_PERTO])
    cena = visao.ver_agora()
    assert [r["nome"] for r in cena["reconhecidos"]] == ["Joao"]


@pytest.mark.asyncio
async def test_rosto_de_perto_sem_corpo_publica_person_recognized():
    # saudacao, recados, diario e quem_fala ESCUTAM este evento: sem ele, quem
    # chegava perto da camera nunca era cumprimentado nem recebia recado.
    _, eventos = await _visao([], [JOAO_DE_PERTO])
    assert len(eventos) == 1
    assert eventos[0]["nome"] == "Joao"
    assert eventos[0]["centro_x"] == pytest.approx(320 / 640)


@pytest.mark.asyncio
async def test_um_rosto_nao_da_nome_a_duas_pessoas():
    # Joao e Ana lado a lado; a caixa da Ana encosta na beira do rosto do Joao.
    joao = Deteccao("person", 0.9, (100, 50, 300, 470))
    ana = Deteccao("person", 0.9, (280, 50, 520, 470))
    rosto_joao = RostoReconhecido(
        pessoa_id=1, nome="Joao", confianca=0.6, caixa=(150, 80, 290, 220)
    )
    identidades = VisionCore._associar_identidades([joao, ana], [rosto_joao])
    assert identidades.get(joao.caixa) == (1, "Joao")
    assert ana.caixa not in identidades


# ------------------------------------------------ 4. cego x sala vazia


@pytest.mark.asyncio
async def test_visao_cega_diz_por_que_e_sala_vazia_nao():
    visao, _ = await _visao([], [])
    assert visao.motivo_cego() is None  # enxergando, so nao ha nada
    visao.pausar()
    assert "pausada" in visao.motivo_cego()


def test_planner_diz_que_esta_cego_em_vez_de_nada():
    from orion.mission.mission_planner import MissionPlanner

    planner = MissionPlanner.__new__(MissionPlanner)
    planner._consultar_visao = None
    planner._motivo_cego = None
    planner.registrar_consultar_visao(lambda: None, motivo_cego=lambda: "a camera nao abriu")
    resposta = asyncio.run(planner._responder_sobre_o_que_ve("o que voce esta vendo?"))
    assert "nao abriu" in resposta
    assert "nada" not in resposta


# ------------------------------------------------ 5. onde o modelo mora


def test_modelo_no_projeto_vira_caminho_absoluto(tmp_path):
    from orion.vision.modulo import _nome_do_arquivo_do_modelo

    (tmp_path / "yolov8n.pt").write_bytes(b"x")
    assert _nome_do_arquivo_do_modelo("yolov8n", tmp_path) == str(tmp_path / "yolov8n.pt")


def test_modelo_fora_do_projeto_avisa_e_cai_no_nome(tmp_path, caplog):
    from orion.vision.modulo import _nome_do_arquivo_do_modelo

    with caplog.at_level("WARNING"):
        assert _nome_do_arquivo_do_modelo("yolov8n", tmp_path) == "yolov8n.pt"
    assert "BAIXAR" in caplog.text


def test_caminho_explicito_na_config_e_respeitado(tmp_path):
    from orion.vision.modulo import _nome_do_arquivo_do_modelo

    explicito = "data/modelos/yolov8n.pt"
    assert _nome_do_arquivo_do_modelo(explicito, tmp_path) == explicito
