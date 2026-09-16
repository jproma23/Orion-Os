"""Testes da Fusao de Sensores (Cap 12 s.8).

Odometria basica = passos por software + BUSSOLA (decisao do dono 2026-08-17).
O encoder fisico foi cancelado, entao a pose sempre viaja como ESTIMATIVA
(`medida: false`, `origem: passos_comandados`); a orientacao usa o rumo
ABSOLUTO da bussola quando valido e, sem ele, volta a ser integrada do delta
diferencial. Toda a odometria e validada com telemetria SINTETICA, nunca com
deslocamento real (ver docstring do modulo). A seguranca da IMU saiu em
2026-08-18 junto com a MPU6050 - nao existe mais aqui.
"""
import asyncio
import math

import pytest
import pytest_asyncio

import motion_core.navigation.fusao_sensores as _fusao_mod
from motion_core.navigation.fusao_sensores import ORIGEM_PASSOS_COMANDADOS, FusaoSensores
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
    assert pos["orientacao_origem"] == "integrada"


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


# ---------- orientacao: a bussola manda (2026-08-17) ----------


@pytest.mark.asyncio
async def test_bussola_valida_ancora_a_orientacao_no_norte(cenario):
    """Com rumo valido, a orientacao e o rumo ABSOLUTO da bussola, nao a
    integracao dos passos. Andar reto a 90° (norte magnetico) move y, nao x.
    """
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0, rumo_graus=90.0)
    await cenario.enviar_telemetria(
        passos_esquerda=PASSOS_POR_METRO, passos_direita=PASSOS_POR_METRO, rumo_graus=90.0
    )

    pose = cenario.posicoes[-1]
    assert pose["orientacao_graus"] == pytest.approx(90.0, abs=1e-3)
    assert pose["orientacao_origem"] == "bussola"
    # direcao do movimento segue a bussola: 90° anda no eixo y
    assert pose["x_m"] == pytest.approx(0.0, abs=1e-3)
    assert pose["y_m"] == pytest.approx(1.0, abs=1e-3)


@pytest.mark.asyncio
async def test_bussola_absoluta_ignora_deriva_do_dead_reckoning(cenario):
    """Giro diferencial sem bussola acumularia deriva; com rumo valido a
    orientacao publicada e SEMPRE o rumo, nao o delta integrado."""
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0, rumo_graus=200.0)
    # direita anda mais que esquerda - sem bussola giraria para esquerda
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=1200, rumo_graus=200.0)

    pose = cenario.posicoes[-1]
    assert pose["orientacao_graus"] == pytest.approx(200.0, abs=1e-3)
    assert pose["orientacao_origem"] == "bussola"


@pytest.mark.asyncio
async def test_bussola_atravessando_0_360_nao_inventa_giro(cenario):
    """Rumo 350 -> 10 atravessa o norte magnetico. Como o rumo e absoluto,
    nao ha 340 graus de giro fantasma: a orientacao vira 10, direto."""
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0, rumo_graus=350.0)
    await cenario.enviar_telemetria(
        passos_esquerda=PASSOS_POR_METRO, passos_direita=PASSOS_POR_METRO, rumo_graus=10.0
    )

    pose = cenario.posicoes[-1]
    assert pose["orientacao_graus"] == pytest.approx(10.0, abs=1e-3)
    assert pose["orientacao_origem"] == "bussola"


@pytest.mark.asyncio
async def test_sem_rumo_orientacao_e_integrada(cenario):
    """Sem bussola (ausente/descalibrada), a orientacao volta ao dead
    reckoning - a pose existe, so perde a ancora no norte."""
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=1200)

    pose = cenario.posicoes[-1]
    assert pose["orientacao_origem"] == "integrada"
    assert pose["orientacao_graus"] > 0


@pytest.mark.asyncio
async def test_bussola_chegando_na_segunda_leitura_ancora_a_pose(cenario):
    """Bussola que so aparece depois: a primeira leitura integra (0°), a
    segunda com rumo valido ancora a orientacao ao norte sem esperar."""
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(
        passos_esquerda=PASSOS_POR_METRO, passos_direita=PASSOS_POR_METRO, rumo_graus=45.0
    )

    pose = cenario.posicoes[-1]
    assert pose["orientacao_graus"] == pytest.approx(45.0, abs=1e-3)
    assert pose["orientacao_origem"] == "bussola"


# ---------- a pose declara que e ESTIMATIVA (encoder cancelado) ----------


@pytest.mark.asyncio
async def test_pose_por_passos_comandados_nao_se_declara_medida(cenario):
    """REGRESSAO do que a interface afirmou em producao em 2026-07-30.

    Com os motores FISICAMENTE DESCONECTADOS dos drivers e as rodas suspensas,
    a tela mostrava `y = 0.8541 m` e `orientacao = 79.68 graus`. O numero vem
    de `passos_esquerda`, que conta os pulsos que o firmware ENVIA ao TB6600 -
    comando, nao medicao. Nada no laco pode desmentir o comando.

    Com o encoder cancelado (2026-08-17), a pose SEMPRE e estimativa - a
    interface precisa se calar sobre medicao em qualquer caso.
    """
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(
        passos_esquerda=PASSOS_POR_METRO, passos_direita=PASSOS_POR_METRO
    )

    assert cenario.posicoes, "nenhuma pose publicada"
    pose = cenario.posicoes[-1]
    assert pose["medida"] is False, "pose por passos comandados se declarou medida"
    assert pose["origem"] == ORIGEM_PASSOS_COMANDADOS

