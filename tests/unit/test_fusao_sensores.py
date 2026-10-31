"""Testes da Fusao de Sensores (Cap 12 s.8).

Desde 2026-10-31 os "passos" sao pulsos do ENCODER (`origem: encoder`,
`medida: true`), e a orientacao e a fusao complementar encoder + bussola: o
encoder integra, e a bussola - so quando valida - puxa devagar, convertida da
convencao dela (0 = norte, horario) para o yaw do ROS (0 = leste,
anti-horario). Toda a odometria e validada com telemetria SINTETICA, nunca com
deslocamento real (ver docstring do modulo). A seguranca da IMU saiu em
2026-08-18 junto com a MPU6050 - nao existe mais aqui.
"""
import asyncio
import math

import pytest
import pytest_asyncio

import motion_core.navigation.fusao_sensores as _fusao_mod
from motion_core.navigation.fusao_sensores import ORIGEM_ENCODER, FusaoSensores
from orion.kernel.event_bus import EventBus, Evento

CONFIG_MOTION = {
    # 2546 passos/metro (teorico: TB6600 1/8, roda de 200 mm). Bate com o
    # orion.yaml e com PASSOS_POR_METRO do firmware.
    "steps_per_meter": 2546,
    "odometry_correction_factor": 1.0,
    "wheel_base_m": 0.30,
}

PASSOS_POR_METRO = CONFIG_MOTION["steps_per_meter"]


class Cenario:
    """EventBus real + FusaoSensores, gravando motion.position para as
    assercoes dos testes."""

    def __init__(self, config: dict | None = None) -> None:
        self.bus = EventBus()
        self.fusao = FusaoSensores(self.bus, config or dict(CONFIG_MOTION))
        self.posicoes: list[dict] = []
        self.bus.subscribe("motion.position", self._gravar_posicao)

        # Relogio sintetico: cada chamada a time.monotonic() avanca 0.5s,
        # simulando a cadencia real de telemetria (~500ms). Sem isto, duas
        # telemetrias seguidas caem no MESMO tick do monotonic do Windows
        # (resolucao de ~15.6ms) e o guarda de delta_tempo<=0 descarta a
        # leitura - teste flaky num laco rapido.
        self._tick_s = 0.0
        self._monotonic_original = _fusao_mod.time.monotonic
        _fusao_mod.time.monotonic = self._proximo_tick

    def _proximo_tick(self) -> float:
        self._tick_s += 0.5
        return self._tick_s

    def restaurar_relogio(self) -> None:
        _fusao_mod.time.monotonic = self._monotonic_original

    async def _gravar_posicao(self, evento: Evento) -> None:
        self.posicoes.append(evento.dados)

    async def enviar_telemetria(self, **payload) -> None:
        # mesmo envelope que ComunicacaoService entrega de verdade (Cap 14
        # s.7): o dado fica aninhado em "payload", nao no topo do evento.
        await self.bus.publish("comm.mensagem.telemetry", {"payload": payload})
        await self.bus.aguardar_fila_vazia()


@pytest_asyncio.fixture
async def cenario():
    c = Cenario()
    tarefa = asyncio.create_task(c.bus.iniciar())
    yield c
    c.restaurar_relogio()
    tarefa.cancel()
    try:
        await tarefa
    except asyncio.CancelledError:
        pass


# ---------- odometria (passos comandados) ----------


@pytest.mark.asyncio
async def test_primeira_telemetria_nao_publica_posicao(cenario):
    # primeira leitura so estabelece a base (nao ha delta ainda pra calcular)
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    assert cenario.posicoes == []


@pytest.mark.asyncio
async def test_andar_reto_incrementa_x_sem_girar(cenario):
    # passos iguais -> anda reto no eixo x (orientacao inicial = 0), sem
    # rotacao. Sem bussola a orientacao e integrada e permanece 0.
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(
        passos_esquerda=PASSOS_POR_METRO, passos_direita=PASSOS_POR_METRO
    )

    assert len(cenario.posicoes) == 1
    pos = cenario.posicoes[0]
    assert pos["x_m"] == pytest.approx(1.0, abs=1e-3)
    assert pos["y_m"] == pytest.approx(0.0, abs=1e-3)
    assert pos["orientacao_graus"] == pytest.approx(0.0, abs=1e-3)
    assert pos["orientacao_origem"] == "encoder"


@pytest.mark.asyncio
async def test_roda_direita_anda_mais_gira_para_esquerda(cenario):
    # direita anda mais que esquerda -> robo gira (orientacao aumenta,
    # convencao CCW) - ver comentario da formula em fusao_sensores.py
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=1200)

    pos = cenario.posicoes[0]
    distancia_direita_m = 1200 / PASSOS_POR_METRO
    delta_orientacao_esperado_graus = math.degrees(
        distancia_direita_m / CONFIG_MOTION["wheel_base_m"]
    )
    assert pos["orientacao_graus"] == pytest.approx(delta_orientacao_esperado_graus, rel=1e-3)
    assert pos["orientacao_graus"] > 0


@pytest.mark.asyncio
async def test_fator_correcao_de_calibracao_e_aplicado(cenario):
    config = dict(CONFIG_MOTION)
    config["odometry_correction_factor"] = 2.0
    cenario_calibrado = Cenario(config)
    tarefa = asyncio.create_task(cenario_calibrado.bus.iniciar())
    try:
        await cenario_calibrado.enviar_telemetria(passos_esquerda=0, passos_direita=0)
        await cenario_calibrado.enviar_telemetria(
            passos_esquerda=PASSOS_POR_METRO, passos_direita=PASSOS_POR_METRO
        )
        pos = cenario_calibrado.posicoes[0]
        assert pos["x_m"] == pytest.approx(2.0, abs=1e-3)  # 1m base * fator 2.0
    finally:
        cenario_calibrado.restaurar_relogio()
        tarefa.cancel()


@pytest.mark.asyncio
async def test_telemetria_sem_campos_de_passos_e_ignorada(cenario):
    await cenario.enviar_telemetria(estado="IDLE")  # sem passos_esquerda/direita
    assert cenario.posicoes == []


@pytest.mark.asyncio
async def test_reinicio_do_mega_resincroniza_sem_publicar(cenario):
    """Reinicio se reconhece pelos DOIS contadores zerando no mesmo quadro.

    Ate 2026-07-27 o criterio era "delta negativo", o que estava errado - ver
    o teste da marcha a re logo abaixo.
    """
    await cenario.enviar_telemetria(passos_esquerda=5000, passos_direita=5000)
    # ESP32 reiniciou: passosAcumulados e membro do MotorManager e nasce zerado
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    assert cenario.posicoes == []
    # a partir daqui a base foi resincronizada - proximo delta funciona normal
    await cenario.enviar_telemetria(
        passos_esquerda=PASSOS_POR_METRO, passos_direita=PASSOS_POR_METRO
    )
    assert len(cenario.posicoes) == 1
    assert cenario.posicoes[0]["x_m"] == pytest.approx(1.0, abs=1e-3)


@pytest.mark.asyncio
async def test_marcha_a_re_anda_para_tras_em_vez_de_ser_descartada(cenario):
    """Regressao 2026-07-27: andar de re congelava a pose.

    O firmware DECREMENTA o contador na re
    (motor_manager.h: `passosAcumulados += sentidoFrente ? 1 : -1`), mas a
    fusao tratava todo delta negativo como "reinicio ou overflow" e voltava
    sem atualizar a pose. Na pratica: o robo andava para tras, a posicao
    congelava e o log enchia de "Contagem de passos regrediu".
    """
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(
        passos_esquerda=2 * PASSOS_POR_METRO, passos_direita=2 * PASSOS_POR_METRO
    )
    assert cenario.posicoes[-1]["x_m"] == pytest.approx(2.0, abs=1e-3)

    # 1 metro de re: tem que VOLTAR para 1 metro, nao congelar em 2
    await cenario.enviar_telemetria(
        passos_esquerda=PASSOS_POR_METRO, passos_direita=PASSOS_POR_METRO
    )
    assert len(cenario.posicoes) == 2, "a re foi descartada em vez de atualizar a pose"
    assert cenario.posicoes[-1]["x_m"] == pytest.approx(1.0, abs=1e-3)


@pytest.mark.asyncio
async def test_re_so_de_um_lado_gira_para_o_outro(cenario):
    """Re assimetrica tem que girar o robo, nao virar deslocamento fantasma.

    A orientacao publicada e normalizada para [0, 360), entao um giro
    horario de 57,3 graus aparece como 302,7 - e o mesmo angulo, nao um
    giro quase completo para o outro lado.
    """
    await cenario.enviar_telemetria(passos_esquerda=2000, passos_direita=2000)
    # so a direita recua -> gira no sentido horario (orientacao DIMINUI)
    await cenario.enviar_telemetria(passos_esquerda=2000, passos_direita=800)

    assert len(cenario.posicoes) == 1
    recuo_direita_m = (800 - 2000) / PASSOS_POR_METRO
    esperado_graus = math.degrees(recuo_direita_m / CONFIG_MOTION["wheel_base_m"]) % 360.0
    assert cenario.posicoes[0]["orientacao_graus"] == pytest.approx(esperado_graus, rel=1e-3)


# ---------- orientacao: encoder + bussola (2026-10-31) ----------


@pytest.mark.asyncio
async def test_bussola_valida_alinha_na_convencao_do_ros(cenario):
    """Rumo 0 (norte) e yaw 90 no ROS: andar reto vai para +y, nao +x."""
    await cenario.enviar_telemetria(
        passos_esquerda=0, passos_direita=0, rumo_graus=0.0, rumo_valido=True
    )
    await cenario.enviar_telemetria(
        passos_esquerda=PASSOS_POR_METRO, passos_direita=PASSOS_POR_METRO,
        rumo_graus=0.0, rumo_valido=True,
    )
    pose = cenario.posicoes[-1]
    assert pose["orientacao_graus"] == pytest.approx(90.0, abs=1e-3)
    assert pose["orientacao_origem"] == "encoder+bussola"
    assert pose["x_m"] == pytest.approx(0.0, abs=1e-3)
    assert pose["y_m"] == pytest.approx(1.0, abs=1e-3)


@pytest.mark.asyncio
async def test_virar_a_direita_na_bussola_diminui_o_yaw(cenario):
    """REGRESSAO do sinal: o rumo cresce no HORARIO; o yaw, no anti-horario.
    Antes, o rumo entrava cru - virar a direita fazia o mapa girar a esquerda."""
    await cenario.enviar_telemetria(
        passos_esquerda=0, passos_direita=0, rumo_graus=0.0, rumo_valido=True
    )
    for _ in range(40):  # parado, a bussola agora diz leste (90)
        await cenario.enviar_telemetria(
            passos_esquerda=0, passos_direita=0, rumo_graus=90.0, rumo_valido=True
        )
    # convergiu para yaw 0 (leste) vindo de 90 - DIMINUIU
    assert cenario.posicoes[-1]["orientacao_graus"] % 360 == pytest.approx(0.0, abs=0.5) or (
        cenario.posicoes[-1]["orientacao_graus"] == pytest.approx(360.0, abs=0.5)
    )


@pytest.mark.asyncio
async def test_bussola_sem_calibracao_e_ignorada(cenario):
    """REGRESSAO: a telemetria completa manda rumo_graus mesmo SEM calibracao,
    e ele era usado. `rumo_valido: false` tem que deixar so o encoder."""
    await cenario.enviar_telemetria(
        passos_esquerda=0, passos_direita=0, rumo_graus=200.0, rumo_valido=False
    )
    await cenario.enviar_telemetria(
        passos_esquerda=PASSOS_POR_METRO, passos_direita=PASSOS_POR_METRO,
        rumo_graus=200.0, rumo_valido=False,
    )
    pose = cenario.posicoes[-1]
    assert pose["orientacao_origem"] == "encoder"
    assert pose["orientacao_graus"] == pytest.approx(0.0, abs=1e-3)
    assert pose["x_m"] == pytest.approx(1.0, abs=1e-3)


@pytest.mark.asyncio
async def test_depois_de_alinhada_a_bussola_nao_da_salto(cenario, monkeypatch):
    """Alinhada uma vez, uma leitura de bussola discrepante (ruido do motor)
    nao vira salto de pose: ela so puxa devagar. TAU enorme isola isso do
    relogio - o asyncio tambem chama o monotonic que o Cenario acelera."""
    monkeypatch.setattr(_fusao_mod, "TAU_BUSSOLA_S", 1e9)
    await cenario.enviar_telemetria(
        passos_esquerda=0, passos_direita=0, rumo_graus=350.0, rumo_valido=True
    )
    await cenario.enviar_telemetria(
        passos_esquerda=0, passos_direita=0, rumo_graus=10.0, rumo_valido=True
    )
    # rumo 350 = yaw 100; a leitura de 10 (yaw 80) quase nao mexe
    assert cenario.posicoes[-1]["orientacao_graus"] == pytest.approx(100.0, abs=0.01)


@pytest.mark.asyncio
async def test_bussola_atravessando_o_norte_vai_pelo_lado_curto(cenario):
    """Rumo 350 -> 10 sao 20 graus, nao 340: convergindo, o yaw para em 80
    sem dar a volta."""
    await cenario.enviar_telemetria(
        passos_esquerda=0, passos_direita=0, rumo_graus=350.0, rumo_valido=True
    )
    vistos = []
    for _ in range(40):
        await cenario.enviar_telemetria(
            passos_esquerda=0, passos_direita=0, rumo_graus=10.0, rumo_valido=True
        )
        vistos.append(cenario.posicoes[-1]["orientacao_graus"])
    assert vistos[-1] == pytest.approx(80.0, abs=0.5)
    assert all(79.5 <= v <= 100.0 for v in vistos), "deu a volta pelo lado longo"


@pytest.mark.asyncio
async def test_sem_rumo_orientacao_e_do_encoder(cenario):
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=1200)

    pose = cenario.posicoes[-1]
    assert pose["orientacao_origem"] == "encoder"
    assert pose["orientacao_graus"] > 0


@pytest.mark.asyncio
async def test_telemetria_reduzida_sem_campo_valido_usa_o_rumo(cenario):
    """A telemetria reduzida so manda rumo_graus quando ele e valido, sem o
    campo rumo_valido - ai a presenca do rumo ja diz que vale."""
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0, rumo_graus=0.0)
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0, rumo_graus=0.0)
    assert cenario.posicoes[-1]["orientacao_origem"] == "encoder+bussola"


# ---------- a pose e MEDIDA pelo encoder (2026-10-31) ----------


@pytest.mark.asyncio
async def test_pose_do_encoder_se_declara_medida(cenario):
    """Os passos sao pulsos do encoder desde a volta do motor DC: medicao da
    roda. A pose de 2026-07-30 (85 cm com os motores desconectados) vinha de
    passos COMANDADOS - com encoder, motor sem girar nao conta pulso."""
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(
        passos_esquerda=PASSOS_POR_METRO, passos_direita=PASSOS_POR_METRO
    )
    pose = cenario.posicoes[-1]
    assert pose["medida"] is True
    assert pose["origem"] == ORIGEM_ENCODER
