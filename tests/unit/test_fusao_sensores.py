"""Testes da Fusao de Sensores (Cap 12 s.8).

Sem motores/encoders fisicos montados ainda (ver docstring do modulo) -
toda a odometria aqui e validada com telemetria SINTETICA (passos de
encoder inventados), nunca com deslocamento real. A parte de seguranca da
IMU usa os mesmos campos que o firmware real ja envia
(`inclinacao_graus`/`impacto_detectado`), entao esses testes descrevem o
comportamento esperado tambem com o Mega fisico.
"""
import asyncio
import math

import pytest
import pytest_asyncio

from motion_core.navigation.fusao_sensores import FusaoSensores
from orion.kernel.event_bus import Evento, EventBus

CONFIG_MOTION = {
    "steps_per_meter": 4000,
    "odometry_correction_factor": 1.0,
    "wheel_base_m": 0.30,
    "tilt_limit_degrees": 20,
}


class Cenario:
    """EventBus real + FusaoSensores, gravando motion.position e os
    eventos de seguranca publicados para as asserçoes dos testes."""

    def __init__(self, config: dict | None = None) -> None:
        self.bus = EventBus()
        self.fusao = FusaoSensores(self.bus, config or dict(CONFIG_MOTION))
        self.posicoes: list[dict] = []
        self.eventos_seguranca: list[Evento] = []
        self.bus.subscribe("motion.position", self._gravar_posicao)
        self.bus.subscribe("safety.safe_mode_entered", self._gravar_seguranca)
        self.bus.subscribe("safety.safe_mode_exited", self._gravar_seguranca)

    async def _gravar_posicao(self, evento: Evento) -> None:
        self.posicoes.append(evento.dados)

    async def _gravar_seguranca(self, evento: Evento) -> None:
        self.eventos_seguranca.append(evento)

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
    tarefa.cancel()
    try:
        await tarefa
    except asyncio.CancelledError:
        pass


# ---------- odometria ----------


@pytest.mark.asyncio
async def test_primeira_telemetria_nao_publica_posicao(cenario):
    # primeira leitura so estabelece a base (nao ha delta ainda pra calcular)
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    assert cenario.posicoes == []


@pytest.mark.asyncio
async def test_andar_reto_incrementa_x_sem_girar(cenario):
    # 4000 passos/metro (default), esquerda e direita iguais -> anda reto no
    # eixo x (orientacao inicial = 0), sem rotacao.
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(passos_esquerda=4000, passos_direita=4000)

    assert len(cenario.posicoes) == 1
    pos = cenario.posicoes[0]
    assert pos["x_m"] == pytest.approx(1.0, abs=1e-3)
    assert pos["y_m"] == pytest.approx(0.0, abs=1e-3)
    assert pos["orientacao_graus"] == pytest.approx(0.0, abs=1e-3)


@pytest.mark.asyncio
async def test_roda_direita_anda_mais_gira_para_esquerda(cenario):
    # direita anda mais que esquerda -> robo gira (orientacao aumenta,
    # convencao CCW) - ver comentario da formula em fusao_sensores.py
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=1200)

    pos = cenario.posicoes[0]
    distancia_direita_m = 1200 / CONFIG_MOTION["steps_per_meter"]
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
        await cenario_calibrado.enviar_telemetria(passos_esquerda=4000, passos_direita=4000)
        pos = cenario_calibrado.posicoes[0]
        assert pos["x_m"] == pytest.approx(2.0, abs=1e-3)  # 1m base * fator 2.0
    finally:
        tarefa.cancel()


@pytest.mark.asyncio
async def test_telemetria_sem_campos_de_encoder_e_ignorada(cenario):
    await cenario.enviar_telemetria(estado="IDLE")  # sem passos_esquerda/direita
    assert cenario.posicoes == []


@pytest.mark.asyncio
async def test_contagem_regressiva_de_passos_resincroniza_sem_publicar(cenario):
    await cenario.enviar_telemetria(passos_esquerda=5000, passos_direita=5000)
    # Mega reiniciou -> contador voltou a zero (delta negativo)
    await cenario.enviar_telemetria(passos_esquerda=100, passos_direita=100)
    assert cenario.posicoes == []
    # a partir daqui a base foi resincronizada - proximo delta funciona normal
    await cenario.enviar_telemetria(passos_esquerda=4100, passos_direita=4100)
    assert len(cenario.posicoes) == 1
    assert cenario.posicoes[0]["x_m"] == pytest.approx(1.0, abs=1e-3)


# ---------- seguranca da IMU ----------


@pytest.mark.asyncio
async def test_sem_imu_conectada_nao_gera_evento(cenario):
    await cenario.enviar_telemetria(
        passos_esquerda=0, passos_direita=0, imu_conectado=False
    )
    assert cenario.eventos_seguranca == []


@pytest.mark.asyncio
async def test_inclinacao_acima_do_limite_publica_safe_mode_entered(cenario):
    await cenario.enviar_telemetria(
        passos_esquerda=0,
        passos_direita=0,
        imu_conectado=True,
        inclinacao_graus=25.0,
        impacto_detectado=False,
    )
    assert len(cenario.eventos_seguranca) == 1
    evento = cenario.eventos_seguranca[0]
    assert evento.topico == "safety.safe_mode_entered"
    assert evento.dados["motivo"] == "inclinacao_perigosa"


@pytest.mark.asyncio
async def test_impacto_detectado_publica_safe_mode_entered(cenario):
    await cenario.enviar_telemetria(
        passos_esquerda=0,
        passos_direita=0,
        imu_conectado=True,
        inclinacao_graus=2.0,
        impacto_detectado=True,
    )
    assert len(cenario.eventos_seguranca) == 1
    assert cenario.eventos_seguranca[0].dados["motivo"] == "impacto_detectado"


@pytest.mark.asyncio
async def test_inclinacao_normal_nao_gera_evento(cenario):
    await cenario.enviar_telemetria(
        passos_esquerda=0,
        passos_direita=0,
        imu_conectado=True,
        inclinacao_graus=5.0,
        impacto_detectado=False,
    )
    assert cenario.eventos_seguranca == []


@pytest.mark.asyncio
async def test_safe_mode_so_publica_uma_vez_enquanto_perigo_persiste(cenario):
    for _ in range(3):
        await cenario.enviar_telemetria(
            passos_esquerda=0,
            passos_direita=0,
            imu_conectado=True,
            inclinacao_graus=30.0,
            impacto_detectado=False,
        )
    entrados = [e for e in cenario.eventos_seguranca if e.topico == "safety.safe_mode_entered"]
    assert len(entrados) == 1  # nao republica a cada telemetria (borda de subida so)


@pytest.mark.asyncio
async def test_safe_mode_exited_ao_normalizar(cenario):
    await cenario.enviar_telemetria(
        passos_esquerda=0,
        passos_direita=0,
        imu_conectado=True,
        inclinacao_graus=30.0,
        impacto_detectado=False,
    )
    await cenario.enviar_telemetria(
        passos_esquerda=0,
        passos_direita=0,
        imu_conectado=True,
        inclinacao_graus=3.0,
        impacto_detectado=False,
    )
    topicos = [e.topico for e in cenario.eventos_seguranca]
    assert topicos == ["safety.safe_mode_entered", "safety.safe_mode_exited"]
