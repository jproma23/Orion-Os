"""Fusao de Sensores (Cap 12 secao 8) - roda no Raspberry Pi.

A cada pacote TELEMETRY recebido do Hardware Core (Arduino), este modulo:
1. Estima a pose 2D do robo (x, y, orientacao) e a velocidade real por
   odometria diferencial (encoders/passos dos motores), publicando o
   resultado como `motion.position`.
2. Usa a IMU (MPU6050) para detectar inclinacao perigosa e impacto, e
   publica um evento critico de seguranca quando isso acontece.

Este e um modulo separado do `NavigationCore` (que ja assina
`comm.mensagem.telemetry` para a checagem tatica de obstaculo frontal) -
"um modulo = uma responsabilidade" (ARQUITETURA.md regra #9): odometria/IMU e
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

        if delta_esquerda < 0 or delta_direita < 0:
            # contador de passos regrediu - so pode ser reinicio do Mega ou
            # overflow do contador. Nao da pra confiar nesse delta: melhor
            # resincronizar a base (ja feito acima) e pular esta leitura do
            # que acumular um deslocamento fantasma em x/y.
            logger.warning(
                "Contagem de passos regrediu (esq=%d dir=%d) - "
                "resincronizando odometria sem atualizar pose",
                delta_esquerda,
                delta_direita,
            )
            return

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

        await self._event_bus.publish(
            "motion.position",
            {
                "x_m": round(self._x_m, 4),
                "y_m": round(self._y_m, 4),
                "orientacao_graus": round(math.degrees(self._orientacao_rad), 2),
                "velocidade_m_s": round(velocidade_m_s, 4),
            },
        )

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
