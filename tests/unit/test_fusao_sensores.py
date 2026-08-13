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
from contextlib import asynccontextmanager

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


# ---------- odometria com encoder de roda (motion.encoder_lado) ----------
#
# Telemetria sintetica, como o resto do arquivo: nenhum encoder fisico foi
# montado ate 2026-08-13. As contas abaixo descrevem a geometria esperada,
# nao uma medicao de bancada - quando houver pulso real, e este arquivo que
# diz o que deveria ter acontecido.

# um encoder so, na roda esquerda, com escala ja medida (1000 pulsos = 1 m)
ENCODER_ESQUERDA = {"encoder_lado": "esquerda", "encoder_pulsos_por_metro": 1000.0}
ENCODER_DIREITA = {"encoder_lado": "direita", "encoder_pulsos_por_metro": 1000.0}


@asynccontextmanager
async def cenario_com(**motion):
    """Cenario com o bloco `motion` ajustado (encoder, escala, bitola)."""
    config = dict(CONFIG_MOTION)
    config.update(motion)
    c = Cenario(config)
    tarefa = asyncio.create_task(c.bus.iniciar())
    try:
        yield c
    finally:
        tarefa.cancel()


@pytest.mark.asyncio
async def test_um_encoder_mede_a_distancia_em_vez_de_afirmar():
    # 2000 passos comandados = 0,5 m; 500 pulsos = 0,5 m medidos. Andando
    # reto os dois concordam - o que este teste fixa e a PROCEDENCIA.
    async with cenario_com(**ENCODER_ESQUERDA) as c:
        await c.enviar_telemetria(passos_esquerda=0, passos_direita=0, pulsos_esquerda=0)
        await c.enviar_telemetria(
            passos_esquerda=2000, passos_direita=2000, pulsos_esquerda=500
        )
        pos = c.posicoes[0]
        assert pos["x_m"] == pytest.approx(0.5, abs=1e-3)
        assert pos["orientacao_graus"] == pytest.approx(0.0, abs=1e-3)
        assert pos["fonte_distancia"] == "encoder"


@pytest.mark.asyncio
async def test_girar_no_proprio_eixo_com_um_encoder_nao_vira_avanco():
    """A armadilha do encoder unico, e a razao de a bitola entrar na conta.

    Girando parado, a roda esquerda recua 0,15 m e a direita avanca 0,15 m
    (bitola 0,30 m, 1 rad). O CENTRO nao sai do lugar. Quem lesse so o
    encoder da esquerda concluiria "recuei 15 cm"; quem tratasse o lado sem
    encoder como zero concluiria "avancei 7,5 cm". Os dois estariam errados.
    """
    async with cenario_com(**ENCODER_ESQUERDA) as c:
        await c.enviar_telemetria(passos_esquerda=0, passos_direita=0, pulsos_esquerda=0)
        await c.enviar_telemetria(
            passos_esquerda=-600, passos_direita=600, pulsos_esquerda=-150
        )
        pos = c.posicoes[0]
        assert pos["x_m"] == pytest.approx(0.0, abs=1e-3)
        assert pos["y_m"] == pytest.approx(0.0, abs=1e-3)
        assert pos["orientacao_graus"] == pytest.approx(math.degrees(1.0), abs=1e-2)


@pytest.mark.asyncio
async def test_encoder_na_direita_usa_o_sinal_oposto_da_bitola():
    # mesmo giro parado do teste anterior, encoder do outro lado: a roda
    # direita avanca 0,15 m e o centro continua parado.
    async with cenario_com(**ENCODER_DIREITA) as c:
        await c.enviar_telemetria(passos_esquerda=0, passos_direita=0, pulsos_direita=0)
        await c.enviar_telemetria(
            passos_esquerda=-600, passos_direita=600, pulsos_direita=150
        )
        pos = c.posicoes[0]
        assert pos["x_m"] == pytest.approx(0.0, abs=1e-3)
        assert pos["orientacao_graus"] == pytest.approx(math.degrees(1.0), abs=1e-2)


@pytest.mark.asyncio
async def test_quando_discordam_o_encoder_manda():
    # o motor recebeu ordem de andar 0,5 m e a roda so girou 0,25 m (passo
    # perdido no TB6600, roda patinando). E esse o caso que justifica o
    # encoder existir - a pose tem que seguir o que girou, nao o que foi
    # mandado girar.
    async with cenario_com(**ENCODER_ESQUERDA) as c:
        await c.enviar_telemetria(passos_esquerda=0, passos_direita=0, pulsos_esquerda=0)
        await c.enviar_telemetria(
            passos_esquerda=2000, passos_direita=2000, pulsos_esquerda=250
        )
        assert c.posicoes[0]["x_m"] == pytest.approx(0.25, abs=1e-3)


@pytest.mark.asyncio
async def test_sem_encoder_a_pose_admite_que_nao_mediu(cenario):
    # comportamento antigo intacto, mas agora declarado: em 2026-07-30 a
    # pose afirmou 85 cm com os motores desconectados, e nada no evento
    # denunciava que aquilo era ordem, nao medicao.
    await cenario.enviar_telemetria(passos_esquerda=0, passos_direita=0)
    await cenario.enviar_telemetria(passos_esquerda=4000, passos_direita=4000)
    pos = cenario.posicoes[0]
    assert pos["x_m"] == pytest.approx(1.0, abs=1e-3)
    assert pos["fonte_distancia"] == "passos_comandados"
    assert pos["fonte_rumo"] == "passos_comandados"


@pytest.mark.asyncio
async def test_escala_nao_medida_recusa_o_encoder_em_vez_de_chutar():
    # encoder_pulsos_por_metro = 0 e o default: ninguem mediu ainda. Usar
    # assim seria dividir por zero; inventar um valor seria pior.
    async with cenario_com(encoder_lado="esquerda", encoder_pulsos_por_metro=0.0) as c:
        await c.enviar_telemetria(passos_esquerda=0, passos_direita=0, pulsos_esquerda=0)
        await c.enviar_telemetria(
            passos_esquerda=4000, passos_direita=4000, pulsos_esquerda=999
        )
        pos = c.posicoes[0]
        assert pos["fonte_distancia"] == "passos_comandados"
        assert pos["x_m"] == pytest.approx(1.0, abs=1e-3)


@pytest.mark.asyncio
async def test_config_promete_encoder_e_a_telemetria_nao_traz_o_campo():
    # firmware velho, ou nome de campo trocado: nao pode travar a odometria
    # nem fingir que o encoder existe.
    async with cenario_com(**ENCODER_ESQUERDA) as c:
        await c.enviar_telemetria(passos_esquerda=0, passos_direita=0)
        await c.enviar_telemetria(passos_esquerda=4000, passos_direita=4000)
        pos = c.posicoes[0]
        assert pos["x_m"] == pytest.approx(1.0, abs=1e-3)
        assert pos["fonte_distancia"] == "passos_comandados"


@pytest.mark.asyncio
async def test_yaw_do_giroscopio_ganha_e_a_volta_de_359_para_1_e_dois_graus():
    # yaw ainda nao existe na telemetria de hoje; quando existir, ele passa
    # na frente ate dos dois encoders (o rumo tirado da diferenca entre
    # rodas e o que mais acumula erro). A conta do menor arco evita que
    # cruzar o zero injete um salto de -358 graus na pose.
    async with cenario_com(encoder_lado="ambos", encoder_pulsos_por_metro=1000.0) as c:
        await c.enviar_telemetria(
            passos_esquerda=0,
            passos_direita=0,
            pulsos_esquerda=0,
            pulsos_direita=0,
            yaw_graus=359.0,
        )
        await c.enviar_telemetria(
            passos_esquerda=0,
            passos_direita=0,
            pulsos_esquerda=0,
            pulsos_direita=0,
            yaw_graus=1.0,
        )
        pos = c.posicoes[0]
        assert pos["orientacao_graus"] == pytest.approx(2.0, abs=1e-2)
        assert pos["fonte_rumo"] == "giroscopio"


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
