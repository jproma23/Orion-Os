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

## Decisao de escopo (atualizada em 2026-08-17)

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

        event_bus.subscribe("comm.mensagem.telemetry", self._ao_receber_telemetria)

    @property
    def pose_atual(self) -> tuple[float, float, float]:
        """(x_m, y_m, orientacao_graus) - util em testes/diagnostico."""
        return (self._x_m, self._y_m, math.degrees(self._orientacao_rad))

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
            rumo_graus = payload.get("rumo_graus")
            if rumo_graus is not None:
                self._orientacao_rad = math.radians(rumo_graus % 360)
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

        metros_por_passo = 1.0 / self._steps_per_meter
        distancia_esquerda_m = delta_esquerda * metros_por_passo * self._fator_correcao
        distancia_direita_m = delta_direita * metros_por_passo * self._fator_correcao

        distancia_centro_m = (distancia_direita_m + distancia_esquerda_m) / 2.0
        # rotacao diferencial classica: roda direita andou mais -> robo gira
        # para a esquerda (orientacao aumenta, convencao matematica CCW).
        delta_orientacao_rad = (distancia_direita_m - distancia_esquerda_m) / self._wheel_base_m

        # ORIENTACAO: a bussola manda (2026-08-17, decisao do dono).
        #
        # O rumo absoluto da bussola cancela a deriva de rotacao do dead
        # reckoning - a pose vira um mapa referenciado ao norte magnetico, e
        # o passo a passo so cuida de QUANTO andou. Sem rumo valido (bussola
        # ausente/descalibrada), a orientacao volta a ser integrada do delta
        # diferencial - a pose continua existindo, so perde a ancora.
        rumo_graus = payload.get("rumo_graus")
        if rumo_graus is not None:
            orientacao_rad = math.radians(rumo_graus % 360)
            orientacao_origem = "bussola"
        else:
            orientacao_rad = (self._orientacao_rad + delta_orientacao_rad) % (2 * math.pi)
            orientacao_origem = "integrada"
        self._orientacao_rad = orientacao_rad

        # integracao "ponto medio": projeta x/y usando a orientacao na METADE
        # do movimento (media entre a orientacao antes e depois do passo),
        # em vez de so a orientacao antiga (Euler simples) - erro menor
        # quando o robo gira e anda no mesmo intervalo de telemetria. Com
        # bussola valida a "antes" e a atual diferem pelo delta do proprio
        # intervalo, que a media suaviza.
        orientacao_anterior_rad = self._orientacao_rad - delta_orientacao_rad
        orientacao_media_rad = orientacao_anterior_rad + delta_orientacao_rad / 2.0
        self._x_m += distancia_centro_m * math.cos(orientacao_media_rad)
        self._y_m += distancia_centro_m * math.sin(orientacao_media_rad)

        velocidade_m_s = distancia_centro_m / delta_tempo_s

        # A pose diz de onde veio: e ESTIMATIVA a partir do que foi
        # comandado (nunca MEDIDA - o encoder fisico foi cancelado), e vai
        # marcada como tal. Quem exibe nao tem como saber isso por conta
        # propria, e foi assim que a interface passou a afirmar 85 cm com os
        # motores desligados.
        await self._event_bus.publish(
            "motion.position",
            {
                "x_m": round(self._x_m, 4),
                "y_m": round(self._y_m, 4),
                "orientacao_graus": round(math.degrees(self._orientacao_rad), 2),
                "velocidade_m_s": round(velocidade_m_s, 4),
                "origem": ORIGEM_PASSOS_COMANDADOS,
                "medida": False,
                "orientacao_origem": orientacao_origem,
            },
        )
