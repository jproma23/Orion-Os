"""Testes da camera na odometria (2026-11-12).

Dois lados:
- Notebook: `OdometriaVisual` mede o giro entre quadros, com as caixas do
  YOLO de quem se mexe sozinho APAGADAS, e o VisionCore publica
  `vision.giro_visual`.
- Pi: `FusaoSensores` usa esse giro como TESTEMUNHA do encoder (sempre) e
  como correcao da orientacao (so com peso_camera > 0).

Tudo com imagem e telemetria SINTETICAS. O FOV da DV20 nao foi medido -
estes testes provam o sinal, a mascara e a fusao, nao a escala real.
"""
import asyncio
import math
import types

import pytest
import pytest_asyncio

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

import motion_core.navigation.fusao_sensores as _fusao_mod  # noqa: E402
from motion_core.navigation.fusao_sensores import FusaoSensores  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.vision.odometria_visual import LARGURA_REDUZIDA, OdometriaVisual  # noqa: E402

# ---------------------------------------------------------------- Notebook


def _cena(semente: int = 0) -> np.ndarray:
    """Quadro 640x480 BGR com textura de comodo (ruido borrado)."""
    gerador = np.random.default_rng(semente)
    cinza = cv2.GaussianBlur((gerador.random((480, 640)) * 255).astype(np.float32), (0, 0), 3)
    cinza = cv2.normalize(cinza, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return cv2.cvtColor(cinza, cv2.COLOR_GRAY2BGR)


def _ruido(frame: np.ndarray, semente: int) -> np.ndarray:
    """Ruido de sensor: camera de verdade nunca repete o quadro exato."""
    gerador = np.random.default_rng(semente)
    ruidoso = frame.astype(np.int16) + gerador.integers(-2, 3, frame.shape)
    return np.clip(ruidoso, 0, 255).astype(np.uint8)


def test_conteudo_para_a_direita_e_giro_para_a_esquerda():
    # A camera virou para a ESQUERDA -> o mundo desliza para a DIREITA na
    # imagem -> giro POSITIVO (anti-horario, a convencao do yaw do Pi).
    odometria = OdometriaVisual(fov_horizontal_graus=60)
    cena = _cena()
    assert odometria.medir(cena).motivo == "primeiro_quadro"
    medida = odometria.medir(_ruido(np.roll(cena, 40, axis=1), 1))
    assert medida.valido
    esperado = 40 / 4 * 60 / LARGURA_REDUZIDA  # 40 px cheios = 10 px reduzidos
    assert medida.delta_graus == pytest.approx(esperado, abs=0.4)
    assert odometria.acumulado_graus == pytest.approx(esperado, abs=0.4)


def test_conteudo_para_a_esquerda_e_giro_negativo():
    odometria = OdometriaVisual(fov_horizontal_graus=60)
    cena = _cena(3)
    odometria.medir(cena)
    medida = odometria.medir(_ruido(np.roll(cena, -40, axis=1), 2))
    assert medida.valido and medida.delta_graus < -3


def test_cena_parada_nao_gira():
    odometria = OdometriaVisual()
    cena = _cena(5)
    odometria.medir(cena)
    medida = odometria.medir(_ruido(cena, 9))
    assert medida.valido
    assert abs(medida.delta_graus) < 0.2


def test_parede_lisa_e_invalido_e_nao_inventa_giro():
    # Parede lisa deu deslocamento LIXO de 57 px com resposta 0,08 na medicao
    # de 12/11 - sem o limiar viraria giro de 20 graus.
    odometria = OdometriaVisual()
    parede = np.full((480, 640, 3), 120, dtype=np.uint8)
    odometria.medir(_ruido(parede, 1))
    medida = odometria.medir(_ruido(parede, 2))
    assert not medida.valido
    assert medida.motivo == "sem_textura"
    assert odometria.acumulado_graus == 0.0
    assert odometria.quadros_invalidos == 1


def test_pessoa_andando_apagada_pelo_yolo_nao_vira_giro():
    # Cenario parado; uma "pessoa" (bloco com textura propria) anda 80 px.
    # Com a caixa do YOLO apagando a pessoa nos dois quadros, o giro e ~0.
    cena = _cena(7)
    pessoa = _cena(99)[100:420, 0:200]

    def com_pessoa(x: int) -> np.ndarray:
        quadro = cena.copy()
        quadro[100:420, x : x + 200] = pessoa
        return quadro

    caixa_antes = (100, 100, 300, 420)
    caixa_depois = (180, 100, 380, 420)

    odometria = OdometriaVisual()
    odometria.medir(com_pessoa(100), [caixa_antes])
    medida = odometria.medir(_ruido(com_pessoa(180), 4), [caixa_depois])
    assert medida.valido
    assert abs(medida.delta_graus) < 0.5


def test_pessoa_cobrindo_quase_tudo_e_invalido():
    odometria = OdometriaVisual()
    cena = _cena(8)
    odometria.medir(cena)
    medida = odometria.medir(_ruido(cena, 1), [(0, 0, 600, 480)])
    assert not medida.valido and medida.motivo == "pouco_cenario"


def test_cabeca_mexeu_e_invalido():
    # O deslizamento e da cabeca, nao do corpo - e a posicao do servo e
    # estimada, entao nao se subtrai: o quadro so nao conta.
    odometria = OdometriaVisual()
    cena = _cena(2)
    odometria.medir(cena, cabeca=(0.0, 0.0))
    medida = odometria.medir(_ruido(np.roll(cena, 40, axis=1), 1), cabeca=(5.0, 0.0))
    assert not medida.valido and medida.motivo == "cabeca_mexeu"
    assert odometria.acumulado_graus == 0.0


def test_quebrar_recomeca_a_base_e_marca_a_janela():
    odometria = OdometriaVisual()
    cena = _cena(4)
    odometria.medir(cena)
    odometria.quebrar()
    assert odometria.quadros_invalidos == 1
    # depois do buraco o proximo quadro e base de novo, nao um giro gigante
    assert odometria.medir(np.roll(cena, 200, axis=1)).motivo == "primeiro_quadro"


def test_evento_leva_acumulado_e_contadores():
    odometria = OdometriaVisual()
    cena = _cena(1)
    odometria.medir(cena)
    medida = odometria.medir(_ruido(np.roll(cena, 40, axis=1), 1))
    evento = odometria.evento(medida, 12.5)
    assert evento["valido"] is True
    assert evento["acumulado_graus"] == pytest.approx(odometria.acumulado_graus, abs=0.01)
    assert evento["quadros_validos"] == 1 and evento["quadros_invalidos"] == 0
    assert evento["timestamp"] == 12.5


# ------------------------------------------------- VisionCore (Notebook)


@pytest.mark.asyncio
async def test_vision_core_publica_giro_e_apaga_a_pessoa_do_yolo():
    pytest.importorskip("ultralytics")
    pytest.importorskip("face_recognition")
    from orion.vision.deteccao import Deteccao
    from orion.vision.vision_core import VisionCore

    class Captura:
        aberta = True

        def __init__(self, quadros):
            self._quadros = list(quadros)

        async def ler_frame(self):
            return self._quadros.pop(0)

    class Detector:
        async def detectar(self, frame):
            # confianca entre os dois limiares: so o de DESCREVER a ve -
            # e mesmo assim tem que ser apagada da conta do giro
            return [Deteccao("person", 0.4, (0, 0, 10, 10))]

    class Reconhecedor:
        async def reconhecer(self, frame_rgb):
            return []

    class OdometriaEspia(OdometriaVisual):
        def __init__(self):
            super().__init__()
            self.caixas = []

        def medir(self, frame, caixas_dinamicas=(), cabeca=(0.0, 0.0)):
            self.caixas.append(list(caixas_dinamicas))
            return super().medir(frame, caixas_dinamicas, cabeca)

    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    giros = []
    bus.subscribe("vision.giro_visual", lambda e: giros.append(e.dados))
    cena = _cena(6)
    odometria = OdometriaEspia()
    visao = VisionCore(
        bus,
        captura=Captura([cena, _ruido(np.roll(cena, 40, axis=1), 1)]),
        detector=Detector(),
        reconhecedor=Reconhecedor(),
        confianca_para_descrever=0.35,
        odometria_visual=odometria,
    )
    await visao.processar_um_frame()
    await visao.processar_um_frame()
    await bus.aguardar_fila_vazia()
    tarefa.cancel()

    assert odometria.caixas[0] == [(0, 0, 10, 10)]
    assert len(giros) == 1  # o primeiro quadro so vira base, nao publica
    assert giros[0]["valido"] is True and giros[0]["delta_graus"] > 3


# ------------------------------------------------------- FusaoSensores (Pi)

CONFIG_MOTION = {"steps_per_meter": 2546, "odometry_correction_factor": 1.0, "wheel_base_m": 0.30}
#: Passos de cada roda (sentidos opostos) para ~10 graus de giro no lugar:
#: 2 * 67/2546 m / 0,30 m = 0,1754 rad.
PASSOS_10_GRAUS = 67
GRAUS_POR_TELEMETRIA = math.degrees(2 * PASSOS_10_GRAUS / 2546 / 0.30)


class CenarioPi:
    def __init__(self, odometria_visual: dict | None = None) -> None:
        config = dict(CONFIG_MOTION)
        if odometria_visual is not None:
            config["odometria_visual"] = odometria_visual
        self.bus = EventBus()
        self.fusao = FusaoSensores(self.bus, config)
        self.posicoes: list[dict] = []
        self.bus.subscribe("motion.position", lambda e: self.posicoes.append(e.dados))
        # relogio sintetico SO da fusao: cada leitura avanca 0,5 s. Trocar o
        # `time.monotonic` global faria o EventBus tambem avancar o relogio
        # (medido: 3 s por evento) e toda medida da camera chegaria "velha".
        self._t = 0.0
        self._original = _fusao_mod.time
        _fusao_mod.time = types.SimpleNamespace(monotonic=self._tick)
        self.passos = 0
        self.camera_acumulado = 0.0
        self.camera_validos = 0
        self.camera_invalidos = 0

    def _tick(self) -> float:
        self._t += 0.5
        return self._t

    def restaurar(self) -> None:
        _fusao_mod.time = self._original

    async def telemetria(self) -> None:
        await self.bus.publish(
            "comm.mensagem.telemetry",
            {"payload": {"passos_esquerda": -self.passos, "passos_direita": self.passos}},
        )
        await self.bus.aguardar_fila_vazia()

    async def camera(self, giro_graus: float, invalido: bool = False) -> None:
        if invalido:
            self.camera_invalidos += 1
        else:
            self.camera_validos += 1
            self.camera_acumulado += giro_graus
        await self.bus.publish(
            "vision.giro_visual",
            {
                "acumulado_graus": self.camera_acumulado,
                "quadros_validos": self.camera_validos,
                "quadros_invalidos": self.camera_invalidos,
            },
        )
        await self.bus.aguardar_fila_vazia()

    async def girar(self, camera_viu_graus: float, invalido: bool = False) -> None:
        """Um passo: a camera manda um quadro, a roda gira ~10 graus, chega telemetria."""
        await self.camera(camera_viu_graus, invalido)
        self.passos += PASSOS_10_GRAUS
        await self.telemetria()


@pytest_asyncio.fixture
async def pi_factory():
    criados = []

    async def criar(odometria_visual=None):
        cenario = CenarioPi(odometria_visual)
        criados.append((cenario, asyncio.create_task(cenario.bus.iniciar())))
        await cenario.telemetria()  # base do encoder
        await cenario.camera(0.0)  # base da camera
        await cenario.telemetria()
        return cenario

    yield criar
    for cenario, tarefa in criados:
        cenario.restaurar()
        tarefa.cancel()


@pytest.mark.asyncio
async def test_camera_confirma_quando_viu_o_giro(pi_factory):
    pi = await pi_factory()
    for _ in range(3):
        await pi.girar(GRAUS_POR_TELEMETRIA)
    assert pi.posicoes[-1]["camera_confirma_giro"] is True
    assert pi.posicoes[-1]["giro_camera_graus"] == pytest.approx(GRAUS_POR_TELEMETRIA, abs=0.01)


@pytest.mark.asyncio
async def test_roda_girou_e_camera_viu_parado_desmente(pi_factory, caplog):
    # a "odometria mentirosa" de 30/07: roda no ar, pose girando
    pi = await pi_factory()
    with caplog.at_level("WARNING"):
        for _ in range(3):
            await pi.girar(0.0)
    assert pi.posicoes[-1]["camera_confirma_giro"] is False
    assert "DESMENTE" in caplog.text
    # peso 0 (o padrao): a camera so testemunha - a pose segue o encoder
    assert pi.posicoes[-1]["orientacao_origem"] == "encoder"
    assert pi.fusao.pose_atual[2] == pytest.approx(3 * GRAUS_POR_TELEMETRIA, abs=0.1)


@pytest.mark.asyncio
async def test_com_peso_a_camera_corrige_a_orientacao_sem_contar_duas_vezes(pi_factory):
    pi = await pi_factory({"peso_camera": 1.0})
    for _ in range(3):
        await pi.girar(0.0)
    # peso 1 e a camera viu parado: a orientacao fica onde estava
    assert pi.fusao.pose_atual[2] % 360 == pytest.approx(0.0, abs=0.1)
    assert pi.posicoes[-1]["orientacao_origem"] == "encoder+camera"

    pi2 = await pi_factory({"peso_camera": 0.5})
    for _ in range(4):
        await pi2.girar(GRAUS_POR_TELEMETRIA)
    # os dois concordam: corrigir nao pode somar o giro de novo
    assert pi2.fusao.pose_atual[2] == pytest.approx(4 * GRAUS_POR_TELEMETRIA, abs=0.1)


@pytest.mark.asyncio
async def test_quadro_invalido_quebra_a_janela(pi_factory):
    pi = await pi_factory({"peso_camera": 1.0})
    await pi.girar(0.0, invalido=True)
    assert pi.posicoes[-1]["giro_camera_graus"] is None
    assert pi.posicoes[-1]["camera_confirma_giro"] is None
    # sem medida valida a camera nao corrige nada
    assert pi.fusao.pose_atual[2] == pytest.approx(GRAUS_POR_TELEMETRIA, abs=0.1)


@pytest.mark.asyncio
async def test_camera_calada_nao_testemunha_nem_corrige(pi_factory):
    pi = await pi_factory({"peso_camera": 1.0})
    for _ in range(4):  # a visao parou de mandar: medida envelhece
        pi.passos += PASSOS_10_GRAUS
        await pi.telemetria()
    assert pi.posicoes[-1]["giro_camera_graus"] is None
    assert pi.posicoes[-1]["camera_confirma_giro"] is None
    assert pi.fusao.pose_atual[2] == pytest.approx(4 * GRAUS_POR_TELEMETRIA, abs=0.1)


@pytest.mark.asyncio
async def test_payload_ruim_da_camera_nao_derruba_a_odometria(pi_factory):
    pi = await pi_factory()
    await pi.bus.publish("vision.giro_visual", {"acumulado_graus": "lixo"})
    await pi.bus.aguardar_fila_vazia()
    pi.passos += PASSOS_10_GRAUS
    await pi.telemetria()
    assert pi.posicoes[-1]["x_m"] == pytest.approx(0.0, abs=1e-3)
