"""Fusao de Sensores (Cap 12 secao 8) - roda no Raspberry Pi.

A cada pacote TELEMETRY recebido do Hardware Core (Arduino), este modulo:
1. Estima a pose 2D do robo (x, y, orientacao) e a velocidade real por
   odometria diferencial (encoders/passos dos motores), publicando o
   resultado como `motion.position`.
2. Usa a IMU (MPU6050) para detectar inclinacao perigosa e impacto, e
   publica um evento critico de seguranca quando isso acontece.

Este e um modulo separado do `NavigationCore` (que ja assina
`comm.mensagem.telemetry` para a checagem tatica de obstaculo frontal) -
"um modulo = uma responsabilidade" (ARQUITETURA.txt regra #9): odometria/IMU e
uma responsabilidade distinta de planejamento de movimento. Os dois
assinam o mesmo topico de telemetria de forma independente pelo Event Bus,
sem se conhecerem (regra #1).

## Decisao de escopo: so odometria diferencial, sem fusao de rumo com a IMU

O Cap 12 s.8 fala em combinar "orientacao... da MPU6050" na fusao. Na
telemetria de hoje (`firmware/hardware_core/include/telemetry_manager.h`)
a IMU so expoe `inclinacao_graus` (um angulo de inclinacao do chassi,
util pra detectar tombamento) e `impacto_detectado` (bool) - NAO ha
yaw/heading nem giroscopio bruto disponivel no pacote. Ou seja, nao existe
hoje um dado de "para onde o robo esta apontando" vindo da IMU pra corrigir
o rumo calculado pelos encoders.

Por isso esta primeira versao calcula (x, y, orientacao) por odometria
diferencial classica pura (delta de passos da roda esquerda/direita ->
distancia percorrida por cada roda -> distancia do centro + rotacao),
usando `motion.steps_per_meter` e `motion.odometry_correction_factor`
(Cap 12 s.9, autocalibracao) do `config/orion.yaml`. `inclinacao_graus` e
`impacto_detectado` sao usados SO para a deteccao de seguranca (item 2
acima), nunca para corrigir x/y/orientacao. Mesmo padrao de "minimo
viavel + gap documentado" usado em FOLLOW/EXPLORE no `navigation_core.py`.
Se o firmware um dia expuser yaw integrado do giroscopio, a fusao de
verdade (encoder + IMU) pode ser adicionada aqui sem mudar o topico
`motion.position` nem quem o consome.

## Sem hardware fisico montado ainda

Nenhum motor/encoder esta fisicamente montado nesta fase (ver
docs/journal.md) - os `passos_esquerda`/`passos_direita` reais valem 0 ou
lixo. Toda a logica de odometria aqui e validada com telemetria sintetica
em teste (`tests/unit/test_fusao_sensores.py`), nunca com deslocamento
real. A logica de seguranca da IMU, por sua vez, PODE ser validada com o
Mega real (ele ja tem MPU6050 conectada de verdade - ver
`docs/project_orion_os_wiring.md`/memoria do projeto).
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

from orion.kernel.event_bus import Evento, EventBus, Prioridade

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
#: Ele esta certo, e o proprio firmware confessa a origem do numero:
#:
#:     long passosAcumulados = 0;  // odometria simples (sem encoder)
#:     roda.passosAcumulados += roda.sentidoFrente ? 1 : -1;   // motor_manager.h
#:
#: `passos_esquerda` / `passos_direita` contam cada pulso que o firmware ENVIA
#: ao TB6600. E comando, nao medicao: com as rodas suspensas, com o rele aberto
#: ou sem motor nenhum ligado, os passos correm e a pose "anda". Integrar isso e
#: dead reckoning sem realimentacao - nao existe nada no laco que possa
#: desmentir o comando.
#:
#: Por isso a pose passa a viajar DECLARANDO a origem. Quem exibe decide o que
#: fazer com um numero nao medido; hoje a webui deixa de mostra-lo como fato.
ORIGEM_PASSOS_COMANDADOS = "passos_comandados"
#: Pulso lido na roda - deslocamento que de fato aconteceu.
ORIGEM_ENCODER = "encoder"

#: Campos que o firmware precisaria mandar para a pose virar MEDIDA.
#:
#: O `EncoderManager` JA existe no firmware, JA e iniciado (`main.cpp:333`) e JA
#: conta pulso real por interrupcao nos pinos 18/19 - e o `telemetry_manager.h`
#: manda os campos do MotorManager, nao os dele. O dado certo e coletado e
#: ninguem le, o padrao que mais se repete neste projeto.
#:
#: Falta, para valer: (1) a telemetria carregar estes campos; (2) os sensores
#: opticos instalados - CONFERIR tensao e se a saida e NPN ou PNP, porque PNP em
#: 12V queima o pino do Mega; (3) calibrar pulso->metro, que depende do disco
#: usado. Enquanto (3) nao for MEDIDO na bancada, converter pulso em metro seria
#: afirmar antes de medir - trocar uma mentira por outra.
CAMPOS_ENCODER = ("pulsos_esquerdo", "pulsos_direito")


class FusaoSensores:
    """Combina odometria (encoders) e seguranca da IMU a cada telemetria."""

    def __init__(self, event_bus: EventBus, config_motion: dict[str, Any]) -> None:
        self._event_bus = event_bus
        self._steps_per_meter: float = config_motion["steps_per_meter"]
        self._fator_correcao: float = config_motion["odometry_correction_factor"]
        self._wheel_base_m: float = config_motion["wheel_base_m"]
        self._limite_inclinacao_graus: float = config_motion["tilt_limit_degrees"]

        # pose acumulada (referencial: origem e orientacao 0 = onde o robo
        # estava quando este modulo foi criado / primeira telemetria chegou)
        self._x_m = 0.0
        self._y_m = 0.0
        self._orientacao_rad = 0.0

        # base para calcular o delta entre duas leituras de telemetria
        # consecutivas - None ate a primeira leitura chegar
        self._passos_esquerda_anterior: int | None = None
        self._passos_direita_anterior: int | None = None
        self._instante_anterior_s: float | None = None

        # evita publicar safety.safe_mode_entered repetidamente a cada
        # telemetria (chega a cada 500ms) enquanto o perigo persiste -
        # so publica na borda de subida/descida (Cap 18 s.9)
        self._safe_mode_ativo = False

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
        await self._checar_seguranca_imu(payload)

    # ---------- odometria diferencial (Cap 12 s.8: encoders/passos) ----------

    async def _atualizar_odometria(self, payload: dict[str, Any]) -> None:
        passos_esquerda = payload.get("passos_esquerda")
        passos_direita = payload.get("passos_direita")
        if passos_esquerda is None or passos_direita is None:
            return  # telemetria sem os campos de encoder - nada a fazer

        agora = time.monotonic()

        if self._passos_esquerda_anterior is None:
            # primeira leitura: so guarda a base, ainda nao da pra calcular
            # um delta (nao existe leitura anterior pra comparar)
            self._passos_esquerda_anterior = passos_esquerda
            self._passos_direita_anterior = passos_direita
            self._instante_anterior_s = agora
            return

        delta_esquerda = passos_esquerda - self._passos_esquerda_anterior
        delta_direita = passos_direita - self._passos_direita_anterior
        delta_tempo_s = agora - self._instante_anterior_s

        self._passos_esquerda_anterior = passos_esquerda
        self._passos_direita_anterior = passos_direita
        self._instante_anterior_s = agora

        # REINICIO DO MEGA: os dois contadores voltam a zero no mesmo quadro,
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
                "provavel reinicio do Mega, resincronizando sem atualizar pose",
                delta_esquerda,
                delta_direita,
            )
            return

        # DELTA NEGATIVO E RE LEGITIMA, nao erro.
        #
        # Ate 2026-07-27 este metodo descartava qualquer delta negativo como
        # "reinicio ou overflow" e voltava sem atualizar a pose. Mas o firmware
        # DECREMENTA de proposito quando anda para tras
        # (motor_manager.h: `passosAcumulados += sentidoFrente ? 1 : -1`), o que
        # significa que TODA marcha a re caia neste return: a pose congelava e o
        # log enchia de "Contagem de passos regrediu". Desencontro de suposicao
        # entre as duas camadas - o comentario antigo aqui afirmava que so podia
        # ser reinicio, e nunca foi verdade.
        #
        # O sinal do delta e justamente o que a odometria diferencial precisa: a
        # conta abaixo ja trata negativo corretamente (anda para tras em x/y e,
        # se so uma roda inverter, gira). Overflow do `long` de 32 bits nao e
        # preocupacao pratica: a 4000 passos/m daria mais de 500 km.

        if delta_tempo_s <= 0:
            return  # telemetria duplicada ou fora de ordem - evita divisao por zero

        metros_por_passo = 1.0 / self._steps_per_meter
        distancia_esquerda_m = delta_esquerda * metros_por_passo * self._fator_correcao
        distancia_direita_m = delta_direita * metros_por_passo * self._fator_correcao

        distancia_centro_m = (distancia_direita_m + distancia_esquerda_m) / 2.0
        # rotacao diferencial classica: roda direita andou mais -> robo gira
        # para a esquerda (orientacao aumenta, convencao matematica CCW).
        delta_orientacao_rad = (distancia_direita_m - distancia_esquerda_m) / self._wheel_base_m

        # integracao "ponto medio": projeta x/y usando a orientacao na METADE
        # do movimento (media entre a orientacao antes e depois do passo),
        # em vez de so a orientacao antiga (Euler simples) - erro menor
        # quando o robo gira e anda no mesmo intervalo de telemetria.
        orientacao_media_rad = self._orientacao_rad + delta_orientacao_rad / 2.0
        self._x_m += distancia_centro_m * math.cos(orientacao_media_rad)
        self._y_m += distancia_centro_m * math.sin(orientacao_media_rad)
        self._orientacao_rad = (self._orientacao_rad + delta_orientacao_rad) % (2 * math.pi)

        velocidade_m_s = distancia_centro_m / delta_tempo_s

        # A pose diz de onde veio. Sem encoder na telemetria ela e ESTIMATIVA
        # a partir do que foi comandado, e vai marcada como tal - quem exibe
        # nao tem como saber isso por conta propria, e foi assim que a interface
        # passou a afirmar 85 cm com os motores desligados.
        origem = ORIGEM_ENCODER if self._tem_encoder(payload) else ORIGEM_PASSOS_COMANDADOS
        await self._event_bus.publish(
            "motion.position",
            {
                "x_m": round(self._x_m, 4),
                "y_m": round(self._y_m, 4),
                "orientacao_graus": round(math.degrees(self._orientacao_rad), 2),
                "velocidade_m_s": round(velocidade_m_s, 4),
                "origem": origem,
                "medida": origem == ORIGEM_ENCODER,
            },
        )

    @staticmethod
    def _tem_encoder(payload: dict[str, Any]) -> bool:
        """True quando a telemetria trouxe pulso REAL de roda.

        Hoje devolve False em producao: nenhum campo com "pulso" ou "encoder"
        existe no pacote (verificado ao vivo em 2026-07-30). Nao e codigo morto
        - e o contrato que a interface consome, e o unico ponto que precisa
        mudar no dia em que o firmware passar a mandar `CAMPOS_ENCODER`.
        """
        return all(payload.get(campo) is not None for campo in CAMPOS_ENCODER)

    # ---------- seguranca da IMU (Cap 12 s.8 paragrafo 2; Cap 18) ----------

    async def _checar_seguranca_imu(self, payload: dict[str, Any]) -> None:
        if not payload.get("imu_conectado", False):
            return  # sem IMU conectada nesta leitura - nada a avaliar

        inclinacao_graus = payload.get("inclinacao_graus")
        impacto_detectado = bool(payload.get("impacto_detectado", False))
        # nao ha dado de giroscopio/tombamento separado na telemetria hoje
        # (ver docstring do modulo) - inclinacao acima do limite cobre tanto
        # "inclinacao perigosa" quanto o caso extremo de tombamento.
        inclinacao_perigosa = (
            inclinacao_graus is not None and abs(inclinacao_graus) >= self._limite_inclinacao_graus
        )

        perigo = inclinacao_perigosa or impacto_detectado

        if perigo and not self._safe_mode_ativo:
            motivo = "impacto_detectado" if impacto_detectado else "inclinacao_perigosa"
            self._safe_mode_ativo = True
            logger.warning(
                "Evento critico de seguranca via IMU: motivo=%s inclinacao_graus=%s impacto=%s",
                motivo,
                inclinacao_graus,
                impacto_detectado,
            )
            await self._event_bus.publish(
                "safety.safe_mode_entered",
                {
                    "motivo": motivo,
                    "inclinacao_graus": inclinacao_graus,
                    "impacto_detectado": impacto_detectado,
                },
                prioridade=Prioridade.CRITICA,
            )
        elif not perigo and self._safe_mode_ativo:
            self._safe_mode_ativo = False
            logger.info("Condicao de seguranca da IMU normalizada - saindo de SAFE_MODE (fusao)")
            await self._event_bus.publish(
                "safety.safe_mode_exited", {}, prioridade=Prioridade.ALTA
            )
