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

from motion_core.navigation.fusao_sensores import ORIGEM_PASSOS_COMANDADOS, FusaoSensores
from orion.kernel.event_bus import Evento, EventBus

CONFIG_MOTION = {
    "steps_per_meter": 4000,
    # 120 pulsos por metro: bate com os 4000 passos/m acima, entao os testes
    # que mandam 4000 passos e 120 pulsos descrevem o MESMO metro percorrido.
    "encoder_pulses_per_meter": 120,
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
async def test_reinicio_do_mega_resincroniza_sem_publicar(cenario):
    """Reinicio se reconhece pelos DOIS contadores zerando no mesmo quadro.

    Ate 2026-07-27 o criterio era "delta negativo", o que estava errado - ver
    o teste da marcha a re logo abaixo.
    """
    await cenario.enviar_telemetria(passos_esquerda=5000, passos_direita=5000)
    # Mega reiniciou: passosAcumulados e membro do MotorManager e nasce zerado
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    assert cenario.posicoes == []
    # a partir daqui a base foi resincronizada - proximo delta funciona normal
    await cenario.enviar_telemetria(passos_esquerda=4000, passos_direita=4000)
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
    await cenario.enviar_telemetria(passos_esquerda=8000, passos_direita=8000)
    assert cenario.posicoes[-1]["x_m"] == pytest.approx(2.0, abs=1e-3)

    # 4000 passos de re: tem que VOLTAR para 1 metro, nao congelar em 2
    await cenario.enviar_telemetria(passos_esquerda=4000, passos_direita=4000)
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
    recuo_direita_m = (800 - 2000) / CONFIG_MOTION["steps_per_meter"]
    esperado_graus = math.degrees(recuo_direita_m / CONFIG_MOTION["wheel_base_m"]) % 360.0
    assert cenario.posicoes[0]["orientacao_graus"] == pytest.approx(esperado_graus, rel=1e-3)


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


# ---------- a pose declara se foi MEDIDA (2026-07-30) ----------


@pytest.mark.asyncio
async def test_pose_por_passos_comandados_nao_se_declara_medida(cenario):
    """REGRESSAO do que a interface afirmou em producao em 2026-07-30.

    Com os motores FISICAMENTE DESCONECTADOS dos drivers e as rodas suspensas,
    a tela mostrava `y = 0.8541 m` e `orientacao = 79.68 graus`. O numero vem de
    `passos_esquerda`, que conta os pulsos que o firmware ENVIA ao TB6600 -
    comando, nao medicao. Nada no laco pode desmentir o comando.

    O dono: *"nao pode haver deslocamento de medidas e nem de bussola nem de mpu
    nem de webui"*. A pose continua sendo calculada (e a unica estimativa que
    existe hoje), mas viaja declarando o que e, para a interface poder se calar.
    """
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(passos_esquerda=4000, passos_direita=4000)

    assert cenario.posicoes, "nenhuma pose publicada"
    pose = cenario.posicoes[-1]
    assert pose["medida"] is False, "pose por passos comandados se declarou medida"
    assert pose["origem"] == "passos_comandados"


@pytest.mark.asyncio
async def test_pose_com_pulso_de_roda_se_declara_medida(cenario):
    """Com pulso REAL de roda na telemetria, a pose passa a valer como medida.

    O `EncoderManager` ja existe no firmware e conta pulso por interrupcao nos
    pinos 18/19 - o que falta e o `telemetry_manager.h` mandar os campos (hoje
    ele manda os do MotorManager). Este teste fixa o contrato para o dia em que
    isso for gravado, para o campo nao nascer errado.
    """
    await cenario.enviar_telemetria(
        passos_esquerda=0, passos_direita=0, pulsos_esquerdo=0, pulsos_direito=0
    )
    await cenario.enviar_telemetria(
        passos_esquerda=4000, passos_direita=4000, pulsos_esquerdo=120, pulsos_direito=120
    )

    pose = cenario.posicoes[-1]
    assert pose["medida"] is True
    assert pose["origem"] == "encoder"


# ---------------------------------------------------------------------------
# A pose para de mentir (2026-08-02)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_comando_sem_giro_nao_move_a_pose(cenario):
    """O bug de 30/07, virado teste.

    A interface afirmou 85 cm e 80 graus com os motores FISICAMENTE
    DESCONECTADOS: os passos comandados corriam e nada podia desmenti-los.
    Agora o encoder desmente - passo subindo com pulso parado significa roda
    travada, patinando ou sem energia, e a pose NAO pode andar por causa
    disso.
    """
    await cenario.enviar_telemetria(
        passos_esquerda=0, passos_direita=0, pulsos_esquerdo=0, pulsos_direito=0
    )
    await cenario.enviar_telemetria(
        passos_esquerda=4000, passos_direita=4000, pulsos_esquerdo=0, pulsos_direito=0
    )

    pose = cenario.posicoes[-1]
    assert pose["x_m"] == 0.0
    assert pose["y_m"] == 0.0
    assert pose["rodas_confirmadas"] is False


@pytest.mark.asyncio
async def test_sem_encoder_na_telemetria_a_confirmacao_e_desconhecida(cenario):
    """"Nao sei" nao pode virar False.

    Sem sensor instalado, marcar `rodas_confirmadas: False` faria a interface
    acusar roda travada num robo que so nao tem encoder - trocaria uma mentira
    por outra, no sentido oposto.
    """
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(passos_esquerda=4000, passos_direita=4000)

    pose = cenario.posicoes[-1]
    assert pose["rodas_confirmadas"] is None
    assert pose["medida"] is False
    assert pose["x_m"] > 0  # sem encoder, a estimativa antiga continua valendo


@pytest.mark.asyncio
async def test_pulso_sem_calibracao_nao_vira_medida():
    """Ter pulso nao basta: sem saber quantos pulsos valem um metro, converter
    seria inventar a escala - trocar uma mentira por outra, que e o que este
    arquivo se recusa a fazer desde 30/07."""
    config = dict(CONFIG_MOTION)
    config["encoder_pulses_per_meter"] = None
    c = Cenario(config)
    tarefa = asyncio.create_task(c.bus.iniciar())
    try:
        await c.enviar_telemetria(
            passos_esquerda=0, passos_direita=0, pulsos_esquerdo=0, pulsos_direito=0
        )
        await c.enviar_telemetria(
            passos_esquerda=4000, passos_direita=4000, pulsos_esquerdo=120, pulsos_direito=120
        )
        pose = c.posicoes[-1]
        assert pose["medida"] is False
        assert pose["origem"] == ORIGEM_PASSOS_COMANDADOS
        # Mas a roda GIROU, e isso o encoder sabe dizer mesmo sem escala.
        assert pose["rodas_confirmadas"] is True
    finally:
        c.bus.parar()
        await tarefa


@pytest.mark.asyncio
async def test_re_com_encoder_anda_para_tras(cenario):
    """O sensor indutivo conta chapa e nao sabe o sentido; quem da o SINAL e o
    passo comandado. A magnitude continua vindo do pulso - por isso isto nao
    devolve a mentira pela janela."""
    await cenario.enviar_telemetria(
        passos_esquerda=0, passos_direita=0, pulsos_esquerdo=0, pulsos_direito=0
    )
    await cenario.enviar_telemetria(
        passos_esquerda=-4000, passos_direita=-4000, pulsos_esquerdo=120, pulsos_direito=120
    )

    pose = cenario.posicoes[-1]
    assert pose["medida"] is True
    assert pose["x_m"] == pytest.approx(-1.0, rel=1e-3)
