"""Fusao de Sensores (Cap 12 secao 8) - roda no Raspberry Pi.

A cada pacote TELEMETRY recebido do Hardware Core (ESP32), este modulo:
1. Estima a pose 2D do robo (x, y, orientacao) e a velocidade real por
   odometria diferencial dos PASSOS COMANDADOS aos motores, publicando o
   resultado como `motion.position`.

Este e um modulo separado do `NavigationCore` (que ja assina
`comm.mensagem.telemetry` para a checagem tatica de obstaculo frontal) -
"um modulo = uma responsabilidade" (ARQUITETURA.txt regra #9): odometria e
uma responsabilidade distinta de planejamento de movimento. Os dois
assinam o mesmo topico de telemetria de forma independente pelo Event Bus,
sem se conhecerem (regra #1).

## 2026-10-31: os passos viraram ENCODER - a pose passou a ser MEDIDA

O motor de passo saiu (DC com encoder desde 2026-09-13) e o firmware manda em
`passos_esquerda`/`passos_direita` os PULSOS CONTADOS pelo encoder de cada
roda (`deslocamentoAcumulado`, motor_manager.h). Isso e medicao da roda, nao
comando: com o rele aberto a roda nao gira e a pose nao anda. Com a roda no
ar ela gira e a pose anda - o encoder mede a RODA, nao o chao; quem confere o
chao e o LiDAR. `steps_per_meter` no orion.yaml virou pulsos por metro.

E a ORIENTACAO deixou de ser o rumo cru da bussola, por dois defeitos achados
ao preparar o Nav2:

1. o rumo da bussola cresce no sentido HORARIO e a orientacao daqui (e o yaw
   do ROS) no ANTI-HORARIO - virar a esquerda fazia o mapa girar para a
   direita;
2. o rumo era usado mesmo SEM calibracao (a telemetria completa manda
   `rumo_graus` sempre que a bussola esta conectada).

Agora e a mesma fusao complementar do firmware: o encoder integra a cada
leitura, e a bussola - so se `rumo_valido` - puxa devagar (TAU_BUSSOLA_S)
para o rumo absoluto, convertido para a convencao ENU do ROS (0 = leste,
90 = norte, anti-horario).

## Decisao de escopo (atualizada em 2026-08-17 - historico, ver acima)

Odometria basica = PASSOS POR SOFTWARE + BUSSOLA (decisao do dono). O encoder
fisico foi cancelado, entao a pose viaja marcada como ESTIMATIVA (`origem:
passos_comandados`, `medida: false`). A orientacao usa o rumo ABSOLUTO da
bussola sempre que ele for valido - isso cancela a deriva de rotacao do dead
reckoning e ancora a pose ao norte magnetico. Sem bussola, a orientacao volta
a ser integrada do delta diferencial (malha aberta, pura).

A navegacao NAO depende mais de a odometria ser exata: o LiDAR LD06 (no
Raspberry, ver `motion_core/lidar/`) cuida dos obstaculos e do entorno. A
IMU (MPU6050) foi DESCARTADA em 2026-08-18 - nao ha mais inclinacao nem
impacto na telemetria, e a deteccao de seguranca da IMU saiu junto.

## Sem hardware fisico montado ainda

Os motores e a bussola estao fisicamente montados e o firmware manda os
passos reais - mas passos COMANDADOS nao sao medicao. Toda a logica de
odometria aqui e validada com telemetria sintetica em teste
(`tests/unit/test_fusao_sensores.py`), nunca com deslocamento real medido.
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Any

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("motion_core.navigation.fusao_sensores")

#: DE ONDE SAIU A POSE - e se ela pode ser chamada de MEDIDA (2026-07-30).
#:
#: Pedido do dono, ao ver a interface afirmar 85 cm de deslocamento e 80 graus
#: de giro com os motores FISICAMENTE DESCONECTADOS dos drivers:
#:
#:   *"nao pode haver deslocamento de medidas e nem de bussola nem de mpu nem
#:   de webui"*, e *"se a confirmando esta sendo pelo envio de pulsos vai haver
#:   essa mentira na webui, devemos usar dados reais captados pelo sensor para
#:   altera a webui"*.
#:
#: A origem continua sendo o comando - `passos_esquerda`/`passos_direita`
#: contam cada pulso que o firmware ENVIA ao TB6600 (`motor_manager.h`). E
#: comando, nao medicao: com as rodas suspensas, com o rele aberto ou sem
#: motor nenhum ligado, os passos correm e a pose "anda". Integrar isso e
#: dead reckoning sem realimentacao - nao existe nada no laco que possa
#: desmentir o comando.
#:
#: DECISAO DO DONO EM 2026-08-17: o encoder fisico (sensor indutivo NPN + 6
#: chapas) foi CANCELADO. A odometria basica e: passos por software (o que o
#: firmware comanda) + rumo ABSOLUTO da bussola (que cancela a deriva de
#: rotacao do dead reckoning) + LiDAR LD06 para a navegacao (que nao depende
#: mais de a odometria ser exata). Por isso este modulo declara a origem como
#: comando e usa a bussola para a orientacao sempre que o rumo for valido.
ORIGEM_PASSOS_COMANDADOS = "passos_comandados"
#: Desde 2026-10-31: os "passos" sao pulsos contados pelo encoder da roda.
ORIGEM_ENCODER = "encoder"

#: Quao devagar a bussola corrige a orientacao integrada do encoder. A
#: bussola le a 10 Hz e o motor ligado perto dela faz ruido - mais devagar
#: que a malha do firmware (1 s) porque aqui a amostra chega a cada 500 ms.
TAU_BUSSOLA_S = 2.0


#: Padroes de `motion.odometria_visual` (ver orion.yaml) - peso 0 = a camera
#: so testemunha, nao corrige a pose.
PADRAO_ODOMETRIA_VISUAL = {
    "peso_camera": 0.0,
    "validade_s": 1.5,
    "janela_testemunha_s": 2.0,
    "giro_minimo_graus": 15.0,
    "giro_parado_graus": 3.0,
}


def yaw_da_bussola_rad(rumo_graus: float) -> float:
    """Rumo da bussola (0 = norte, HORARIO) -> yaw ENU (0 = leste, anti-horario)."""
    return math.radians((90.0 - rumo_graus) % 360.0)


class FusaoSensores:
    """Combina odometria (passos + bussola) a cada telemetria."""

    def __init__(self, event_bus: EventBus, config_motion: dict[str, Any]) -> None:
        self._event_bus = event_bus
        self._steps_per_meter: float = config_motion["steps_per_meter"]
        self._fator_correcao: float = config_motion["odometry_correction_factor"]
        self._wheel_base_m: float = config_motion["wheel_base_m"]

        # pose acumulada (referencial: origem e orientacao 0 = onde o robo
        # estava quando este modulo foi criado / primeira telemetria chegou).
        # Com bussola valida a orientacao passa a ser ABSOLUTA (graus 0 =
        # norte magnetico) - ver `_atualizar_odometria`.
        self._x_m = 0.0
        self._y_m = 0.0
        self._orientacao_rad = 0.0

        # base para calcular o delta entre duas leituras de telemetria
        # consecutivas - None ate a primeira leitura chegar
        self._passos_esquerda_anterior: int | None = None
        self._passos_direita_anterior: int | None = None
        self._instante_anterior_s: float | None = None
        #: A orientacao ja foi alinhada a bussola alguma vez? A primeira
        #: leitura valida ALINHA de uma vez; as seguintes so puxam devagar.
        self._ancorada = False
        #: Diferenca (rad) entre o yaw da bussola e o referencial da pose,
        #: fixada quando a bussola so fica valida DEPOIS de o robo andar
        #: (auditoria C2-04). 0 = a pose e absoluta (ancorada parada).
        self._desvio_bussola_rad = 0.0
        #: O robo ja se mexeu desde que a pose nasceu? Depois disso, alinhar
        #: de uma vez com a bussola giraria o mapa inteiro.
        self._ja_andou = False

        # A CAMERA NA ODOMETRIA (2026-11-12). O Notebook mede o giro pela
        # imagem (src/orion/vision/odometria_visual.py) e manda o ACUMULADO.
        # Aqui ele vira duas coisas:
        #   1. TESTEMUNHA: a roda diz que girou e a camera viu o mundo parado
        #      = roda no ar, patinando ou robo preso (a "odometria mentirosa"
        #      de 30/07). Sai em `camera_confirma_giro` e no log.
        #   2. CORRECAO (so com peso_camera > 0): puxa a orientacao para o
        #      giro que a camera viu. Feita pela DIFERENCA entre o que a
        #      camera e o encoder giraram desde a ultima medida usada - e nao
        #      somando os dois - para mensagem atrasada ou dois quadros num
        #      intervalo de telemetria nao contarem o giro duas vezes.
        conf_visual = {**PADRAO_ODOMETRIA_VISUAL, **(config_motion.get("odometria_visual") or {})}
        self._peso_camera = min(1.0, max(0.0, float(conf_visual["peso_camera"])))
        self._validade_camera_s = float(conf_visual["validade_s"])
        self._janela_testemunha_s = float(conf_visual["janela_testemunha_s"])
        self._giro_minimo_graus = float(conf_visual["giro_minimo_graus"])
        self._giro_parado_graus = float(conf_visual["giro_parado_graus"])
        #: Ultima `vision.giro_visual` recebida: (recebida_em, acumulado_graus,
        #: quadros_invalidos, quadros_validos).
        self._camera_ultima: tuple[float, float, int, int] | None = None
        #: A medida da camera a partir da qual se compara (a ultima usada).
        self._camera_base: tuple[float, float, int, int] | None = None
        #: Giro do ENCODER (rad) desde `_camera_base` - o par da comparacao.
        self._encoder_desde_base_rad = 0.0
        #: Epocas fechadas para a testemunha: (instante, giro_encoder_graus,
        #: giro_camera_graus ou None se a camera nao cobriu a epoca).
        self._epocas: deque[tuple[float, float, float | None]] = deque()
        self._desmentido_avisado = False

        event_bus.subscribe("comm.mensagem.telemetry", self._ao_receber_telemetria)
        event_bus.subscribe("vision.giro_visual", self._ao_receber_giro_visual)

    @staticmethod
    def _rumo_valido(payload: dict[str, Any]) -> float | None:
        """O rumo da bussola, ou None se ela nao esta valida.

        A telemetria REDUZIDA so manda `rumo_graus` quando ele e valido, sem
        o campo `rumo_valido`; a COMPLETA manda os dois, e o rumo mesmo sem
        calibracao. Por isso: ausente = valido so se o rumo veio.
        """
        rumo = payload.get("rumo_graus")
        if rumo is None or payload.get("rumo_valido", True) is False:
            return None
        return float(rumo)

    @property
    def pose_atual(self) -> tuple[float, float, float]:
        """(x_m, y_m, orientacao_graus) - util em testes/diagnostico."""
        return (self._x_m, self._y_m, math.degrees(self._orientacao_rad))

    async def _ao_receber_giro_visual(self, evento: Evento) -> None:
        dados = evento.dados or {}
        try:
            self._camera_ultima = (
                time.monotonic(),
                float(dados["acumulado_graus"]),
                int(dados["quadros_invalidos"]),
                int(dados["quadros_validos"]),
            )
        except (KeyError, TypeError, ValueError):
            # payload de outra maquina: dado ruim nao derruba a odometria
            logger.warning("vision.giro_visual invalido: %r", dados)

    def _camera_fresca(self, agora: float) -> tuple[float, float, int, int] | None:
        ultima = self._camera_ultima
        if ultima is None or agora - ultima[0] > self._validade_camera_s:
            return None
        return ultima

    def _comparar_com_camera(self, agora: float, delta_encoder_rad: float) -> float | None:
        """Fecha uma epoca se chegou medida nova da camera.

        Devolve o giro da camera (rad) da epoca - ou None se nao ha medida
        nova (a epoca continua aberta, acumulando encoder) ou se a janela
        quebrou (quadro invalido, camera velha): ai a base recomeca.
        """
        self._encoder_desde_base_rad += delta_encoder_rad
        atual = self._camera_fresca(agora)
        base = self._camera_base
        if atual is None:
            if base is not None:
                self._epocas.append((agora, math.degrees(self._encoder_desde_base_rad), None))
            self._camera_base = None
            self._encoder_desde_base_rad = 0.0
            return None
        if base is None:
            self._camera_base = atual
            self._encoder_desde_base_rad = 0.0
            return None
        if atual[3] == base[3] and atual[2] == base[2]:
            return None  # nenhum quadro novo desde a base: epoca segue aberta
        giro_encoder_graus = math.degrees(self._encoder_desde_base_rad)
        self._camera_base = atual
        self._encoder_desde_base_rad = 0.0
        if atual[2] != base[2]:
            # houve quadro invalido no meio (cabeca mexeu, sem textura,
            # buraco): o acumulado nao cobre a epoca inteira
            self._epocas.append((agora, giro_encoder_graus, None))
            return None
        giro_camera_graus = atual[1] - base[1]
        self._epocas.append((agora, giro_encoder_graus, giro_camera_graus))
        return math.radians(giro_camera_graus)

    def _testemunho(self, agora: float) -> bool | None:
        """A camera confirma o giro da janela? None = nao ha o que confirmar
        (giro pequeno) ou a camera nao cobriu a janela inteira."""
        while self._epocas and agora - self._epocas[0][0] > self._janela_testemunha_s:
            self._epocas.popleft()
        if not self._epocas or any(c is None for _, _, c in self._epocas):
            return None
        giro_encoder = sum(e for _, e, _ in self._epocas)
        giro_camera = sum(c for _, _, c in self._epocas)
        if abs(giro_encoder) >= self._giro_minimo_graus:
            confirma = abs(giro_camera) >= self._giro_parado_graus
        elif abs(giro_camera) >= self._giro_minimo_graus:
            # a camera viu girar e a roda nao: empurrado, carregado
            confirma = abs(giro_encoder) >= self._giro_parado_graus
        else:
            return None
        if not confirma and not self._desmentido_avisado:
            self._desmentido_avisado = True
            logger.warning(
                "camera DESMENTE o encoder: encoder %.1f graus, camera %.1f graus em %.1f s "
                "- roda no ar, patinando, robo preso ou empurrado",
                giro_encoder,
                giro_camera,
                self._janela_testemunha_s,
            )
        elif confirma:
            self._desmentido_avisado = False
        return confirma

    async def _ao_receber_telemetria(self, evento: Evento) -> None:
        # TELEMETRY nao passa pela normalizacao de topico das EVENT (Cap 14
        # s.7) - o dado real fica aninhado em "payload" (Mensagem.to_dict()),
        # mesmo detalhe ja documentado em NavigationCore._ao_atualizar_telemetria.
        payload = evento.dados.get("payload", {})
        await self._atualizar_odometria(payload)

    # ---------- odometria diferencial (Cap 12 s.8: passos comandados) ----------

    async def _atualizar_odometria(self, payload: dict[str, Any]) -> None:
        passos_esquerda = payload.get("passos_esquerda")
        passos_direita = payload.get("passos_direita")
        if passos_esquerda is None or passos_direita is None:
            return  # telemetria sem os campos de passos - nada a fazer

        agora = time.monotonic()

        if self._passos_esquerda_anterior is None:
            # primeira leitura: so guarda a base, ainda nao da pra calcular
            # um delta (nao existe leitura anterior pra comparar)
            self._passos_esquerda_anterior = passos_esquerda
            self._passos_direita_anterior = passos_direita
            self._instante_anterior_s = agora
            # Se a bussola ja tem rumo, a orientacao nasce absoluta - evita
            # um salto de quadro quando o rumo valido chegar na amostra
            # seguinte (a integracao comecaria do referencial local e a
            # bussola corrigiria tudo de uma vez).
            rumo_graus = self._rumo_valido(payload)
            if rumo_graus is not None:
                self._orientacao_rad = yaw_da_bussola_rad(rumo_graus)
                self._ancorada = True
            return

        delta_esquerda = passos_esquerda - self._passos_esquerda_anterior
        delta_direita = passos_direita - self._passos_direita_anterior
        delta_tempo_s = agora - self._instante_anterior_s

        self._passos_esquerda_anterior = passos_esquerda
        self._passos_direita_anterior = passos_direita
        self._instante_anterior_s = agora

        # REINICIO DO ESP32: os dois contadores voltam a zero no mesmo quadro,
        # porque `passosAcumulados` e membro do MotorManager e nasce zerado.
        # Essa e a assinatura que se pode confiar - e nao "delta negativo",
        # ver o bloco seguinte.
        if (
            passos_esquerda == 0
            and passos_direita == 0
            and (delta_esquerda != 0 or delta_direita != 0)
        ):
            logger.warning(
                "Contadores de passo zeraram juntos (delta esq=%d dir=%d) - "
                "provavel reinicio do ESP32, resincronizando sem atualizar pose",
                delta_esquerda,
                delta_direita,
            )
            return

        # DELTA NEGATIVO E RE LEGITIMA, nao erro.
        #
        # O firmware DECREMENTA de proposito quando anda para tras
        # (motor_manager.h: `passosAcumulados += sentidoFrente ? 1 : -1`), o
        # que significa que TODA marcha a re tem delta negativo. O sinal do
        # delta e justamente o que a odometria diferencial precisa: a conta
        # abaixo ja trata negativo corretamente (anda para tras em x/y e, se
        # so uma roda inverter, gira). Overflow do `long` de 32 bits nao e
        # preocupacao pratica: a 2546 passos/m daria mais de 800 km.

        if delta_tempo_s <= 0:
            return  # telemetria duplicada ou fora de ordem - evita divisao por zero
        if delta_esquerda or delta_direita:
            self._ja_andou = True

        metros_por_passo = 1.0 / self._steps_per_meter
        distancia_esquerda_m = delta_esquerda * metros_por_passo * self._fator_correcao
        distancia_direita_m = delta_direita * metros_por_passo * self._fator_correcao

        distancia_centro_m = (distancia_direita_m + distancia_esquerda_m) / 2.0
        # rotacao diferencial classica: roda direita andou mais -> robo gira
        # para a esquerda (orientacao aumenta, convencao matematica CCW).
        delta_orientacao_rad = (distancia_direita_m - distancia_esquerda_m) / self._wheel_base_m

        # ORIENTACAO: encoder + bussola em fusao complementar (2026-10-31).
        #
        # O encoder da o giro a cada leitura; a bussola, SO quando valida,
        # puxa devagar para o rumo absoluto. A primeira leitura valida alinha
        # de uma vez - antes dela a orientacao era relativa ao ponto de
        # partida e nao ha o que suavizar.
        orientacao_anterior_rad = self._orientacao_rad
        orientacao_rad = orientacao_anterior_rad + delta_orientacao_rad
        rumo_graus = self._rumo_valido(payload)
        if rumo_graus is not None:
            if not self._ancorada and self._ja_andou:
                # BUSSOLA TARDIA (auditoria C2-04): o robo andou sem bussola e
                # ela chegou agora. Alinhar de uma vez saltava a orientacao
                # (ate 180 graus) e o TF/mapa do Nav2 girava junto. Ela entra
                # RELATIVA: guarda a diferenca deste instante e, dali em
                # diante, so corrige a deriva - o referencial nao muda.
                self._desvio_bussola_rad = yaw_da_bussola_rad(rumo_graus) - orientacao_rad
                logger.warning(
                    "bussola ficou valida com o robo ja andando - entra relativa "
                    "(desvio %.0f graus), sem girar o mapa",
                    math.degrees(self._desvio_bussola_rad),
                )
            alvo_rad = yaw_da_bussola_rad(rumo_graus) - self._desvio_bussola_rad
            erro = alvo_rad - orientacao_rad
            erro = (erro + math.pi) % (2 * math.pi) - math.pi  # o lado mais curto
            peso = 1.0 if not self._ancorada else min(1.0, delta_tempo_s / TAU_BUSSOLA_S)
            orientacao_rad += peso * erro
            self._ancorada = True
            orientacao_origem = "encoder+bussola"
        else:
            orientacao_origem = "encoder"

        # CAMERA (2026-11-12): testemunha sempre; corrige so com peso > 0.
        giro_camera_rad = self._comparar_com_camera(agora, delta_orientacao_rad)
        if giro_camera_rad is not None and self._peso_camera > 0:
            giro_encoder_rad = math.radians(self._epocas[-1][1])
            orientacao_rad += self._peso_camera * (giro_camera_rad - giro_encoder_rad)
            orientacao_origem += "+camera"
        camera_confirma = self._testemunho(agora)
        self._orientacao_rad = orientacao_rad % (2 * math.pi)

        # integracao "ponto medio": projeta x/y usando a orientacao na METADE
        # do giro medido pelo encoder (Euler simples erraria mais quando o
        # robo gira e anda no mesmo intervalo de telemetria).
        orientacao_media_rad = orientacao_anterior_rad + delta_orientacao_rad / 2.0
        self._x_m += distancia_centro_m * math.cos(orientacao_media_rad)
        self._y_m += distancia_centro_m * math.sin(orientacao_media_rad)

        velocidade_m_s = distancia_centro_m / delta_tempo_s

        # A pose diz de onde veio: o encoder MEDE a roda (2026-10-31). Ainda
        # e a roda e nao o chao - roda no ar ou patinando anda aqui e nao no
        # mundo - e e por isso que a conversa confere o movimento pelo LiDAR.
        await self._event_bus.publish(
            "motion.position",
            {
                "x_m": round(self._x_m, 4),
                "y_m": round(self._y_m, 4),
                "orientacao_graus": round(math.degrees(self._orientacao_rad), 2),
                "velocidade_m_s": round(velocidade_m_s, 4),
                "origem": ORIGEM_ENCODER,
                "medida": True,
                "orientacao_origem": orientacao_origem,
                "giro_camera_graus": (
                    None if giro_camera_rad is None else round(math.degrees(giro_camera_rad), 2)
                ),
                "camera_confirma_giro": camera_confirma,
            },
        )
