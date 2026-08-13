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

## Duas perguntas, duas fontes: distancia e rumo

"Quanto andei" e "pra onde estou apontando" sao perguntas DIFERENTES, e a
melhor fonte de cada uma nao e a mesma. Este modulo escolhe, a cada quadro
de telemetria, a melhor fonte disponivel para cada uma, e DECLARA no
`motion.position` qual usou (`fonte_distancia` e `fonte_rumo`).

| pergunta | da melhor para a pior |
|---|---|
| rumo | `yaw_graus` do giroscopio -> dois encoders -> passos comandados |
| distancia | encoder (um ou dois) -> passos comandados |

Passo COMANDADO nao e medicao, e afirmacao: em 2026-07-30 a pose declarou
85 cm e 80 graus com os motores fisicamente desconectados. Enquanto for
essa a fonte, quem consome `motion.position` precisa saber - dai os campos
de procedencia, e nao um numero solto que parece medido.

Rumo tirado da DIFERENCA entre as rodas e a pior fonte das tres: uma
patinada de um lado so corrompe o angulo, e o erro nunca mais sai, ele se
acumula ate o fim da missao. Por isso o giroscopio, quando existir na
telemetria, ganha dos encoders mesmo com dois instalados.

## Um encoder so (`motion.encoder_lado`)

E uma escolha legitima, nao meia-solucao - inclusive por causa do paragrafo
acima. Com o rumo vindo de outra fonte, um encoder basta para a distancia,
porque a geometria diferencial da a distancia do centro de volta:

    d_esquerda = d_centro - (bitola/2) * dtheta
    d_direita  = d_centro + (bitola/2) * dtheta

    logo:  d_centro = d_esquerda + (bitola/2) * dtheta      (encoder a esquerda)
           d_centro = d_direita  - (bitola/2) * dtheta      (encoder a direita)

O termo `(bitola/2) * dtheta` e o que impede o erro grosseiro de girar no
proprio eixo: ali o centro nao anda nada, mas a roda instrumentada gira
bastante - sem essa correcao o robo se declararia avancando enquanto so
roda parado. Isso tambem promove `motion.wheel_base_m` de numero cosmetico
a numero que precisa ser MEDIDO no chassi: com um encoder so, errar a
bitola vira erro de distancia proporcional ao quanto o robo girou.

`inclinacao_graus` e `impacto_detectado` continuam servindo SO a deteccao
de seguranca (item 2 acima), nunca para corrigir x/y/orientacao.

## Sem hardware fisico montado ainda

Nenhum motor/encoder esta fisicamente montado nesta fase (ver
docs/journal.md) - os `passos_esquerda`/`passos_direita` reais valem 0 ou
lixo. Toda a logica de odometria aqui e validada com telemetria sintetica
em teste (`tests/unit/test_fusao_sensores.py`), nunca com deslocamento
real. A logica de seguranca da IMU, por sua vez, PODE ser validada com o
Mega real (ele ja tem MPU6050 conectada de verdade - ver
`docs/project_orion_os_wiring.md`/memoria do projeto).

O caminho do encoder aqui esta pronto e testado com telemetria sintetica,
mas NAO foi visto rodando com pulso real - falta encoder fisico e falta o
firmware publicar `pulsos_esquerda`/`pulsos_direita` (o EncoderManager ja
conta, `encoder_manager.h`, mas os pinos estao reservados sem fio ligado).
`yaw_graus` tambem ainda nao existe na telemetria; enquanto nao existir, o
rumo cai para os passos comandados e o `motion.position` diz isso.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

from orion.kernel.event_bus import Evento, EventBus, Prioridade

logger = logging.getLogger("motion_core.navigation.fusao_sensores")

# valores aceitos em motion.encoder_lado - ver config/orion.yaml
_LADOS_ENCODER_VALIDOS = frozenset({"nenhum", "esquerda", "direita", "ambos"})


class FusaoSensores:
    """Combina odometria (encoders) e seguranca da IMU a cada telemetria."""

    def __init__(self, event_bus: EventBus, config_motion: dict[str, Any]) -> None:
        self._event_bus = event_bus
        self._steps_per_meter: float = config_motion["steps_per_meter"]
        self._fator_correcao: float = config_motion["odometry_correction_factor"]
        self._wheel_base_m: float = config_motion["wheel_base_m"]
        self._limite_inclinacao_graus: float = config_motion["tilt_limit_degrees"]

        # Encoder de roda. Chaves opcionais de proposito: uma instalacao
        # antiga (ou um teste) sem elas continua funcionando exatamente como
        # antes, pelos passos comandados.
        self._encoder_lado: str = str(config_motion.get("encoder_lado", "nenhum")).lower()
        self._encoder_pulsos_por_metro: float = float(
            config_motion.get("encoder_pulsos_por_metro", 0.0)
        )
        if self._encoder_lado not in _LADOS_ENCODER_VALIDOS:
            logger.error(
                "motion.encoder_lado=%r nao e um valor valido (%s) - tratando como "
                "'nenhum'. A distancia continua vindo dos passos comandados.",
                self._encoder_lado,
                ", ".join(sorted(_LADOS_ENCODER_VALIDOS)),
            )
            self._encoder_lado = "nenhum"
        if self._encoder_lado != "nenhum" and self._encoder_pulsos_por_metro <= 0.0:
            # Escala nao medida. Usar assim mesmo seria dividir por zero ou
            # inventar um fator - prefiro recusar o encoder e dizer por que.
            logger.error(
                "motion.encoder_lado=%r mas encoder_pulsos_por_metro=%s - sem a "
                "escala medida nao da pra converter pulso em metro. Ignorando o "
                "encoder; meca empurrando o robo 2 m com o motor desligado.",
                self._encoder_lado,
                self._encoder_pulsos_por_metro,
            )
            self._encoder_lado = "nenhum"

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

        # mesma ideia para os contadores do encoder e para o yaw do
        # giroscopio - cada fonte tem a sua propria base, porque uma pode
        # aparecer/sumir da telemetria sem a outra
        self._pulsos_esquerda_anterior: int | None = None
        self._pulsos_direita_anterior: int | None = None
        self._yaw_anterior_rad: float | None = None
        self._avisou_encoder_ausente = False

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
            # as outras fontes tambem precisam guardar a base agora, senao o
            # primeiro delta delas sairia contra o zero inicial e viraria um
            # salto de pose no segundo quadro de telemetria
            self._distancias_por_encoder(payload)
            self._delta_yaw(payload)
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

        # o que o encoder mediu neste intervalo (None onde nao ha encoder)
        encoder_esquerda_m, encoder_direita_m = self._distancias_por_encoder(payload)

        # ---------------------------- RUMO ----------------------------
        # Ordem de preferencia justificada no cabecalho do modulo. A
        # diferenca entre rodas fica por ultimo de proposito: ela e a unica
        # em que a patinada de UM lado vira erro de angulo permanente.
        delta_yaw_rad = self._delta_yaw(payload)
        if delta_yaw_rad is not None:
            delta_orientacao_rad = delta_yaw_rad
            fonte_rumo = "giroscopio"
        elif encoder_esquerda_m is not None and encoder_direita_m is not None:
            delta_orientacao_rad = (encoder_direita_m - encoder_esquerda_m) / self._wheel_base_m
            fonte_rumo = "encoder"
        else:
            # rotacao diferencial classica: roda direita andou mais -> robo gira
            # para a esquerda (orientacao aumenta, convencao matematica CCW).
            delta_orientacao_rad = (
                distancia_direita_m - distancia_esquerda_m
            ) / self._wheel_base_m
            fonte_rumo = "passos_comandados"

        # -------------------------- DISTANCIA --------------------------
        # Com UM encoder so, a distancia do centro sai da geometria
        # diferencial usando o rumo ja resolvido acima (cabecalho do modulo):
        #   d_centro = d_esquerda + (bitola/2)*dtheta
        #   d_centro = d_direita  - (bitola/2)*dtheta
        # O termo da bitola e o que zera o avanco quando o robo so gira no
        # proprio eixo - sem ele a roda instrumentada viraria "avancei".
        meia_bitola_m = self._wheel_base_m / 2.0
        if encoder_esquerda_m is not None and encoder_direita_m is not None:
            distancia_centro_m = (encoder_direita_m + encoder_esquerda_m) / 2.0
            fonte_distancia = "encoder"
        elif encoder_esquerda_m is not None:
            distancia_centro_m = encoder_esquerda_m + meia_bitola_m * delta_orientacao_rad
            fonte_distancia = "encoder"
        elif encoder_direita_m is not None:
            distancia_centro_m = encoder_direita_m - meia_bitola_m * delta_orientacao_rad
            fonte_distancia = "encoder"
        else:
            distancia_centro_m = (distancia_direita_m + distancia_esquerda_m) / 2.0
            fonte_distancia = "passos_comandados"

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
                # procedencia: "passos_comandados" quer dizer que ninguem
                # MEDIU isto - e o que o robo mandou fazer, nao o que ele fez
                "fonte_distancia": fonte_distancia,
                "fonte_rumo": fonte_rumo,
            },
        )

    # ---------- fontes auxiliares (encoder de roda e yaw do giroscopio) ----------

    def _distancias_por_encoder(
        self, payload: dict[str, Any]
    ) -> tuple[float | None, float | None]:
        """Metros medidos por cada encoder neste intervalo (None onde nao ha).

        Devolve `(esquerda, direita)`. Um lado vem None quando nao ha encoder
        ali - e e exatamente esse None que faz o caso de UM encoder cair no
        ramo da geometria diferencial, em vez de tratar o lado ausente como
        "andou zero" (o que faria o robo se declarar girando o tempo todo).
        """
        if self._encoder_lado == "nenhum":
            return (None, None)

        precisa_esquerda = self._encoder_lado in ("esquerda", "ambos")
        precisa_direita = self._encoder_lado in ("direita", "ambos")

        pulsos_esquerda = payload.get("pulsos_esquerda")
        pulsos_direita = payload.get("pulsos_direita")

        faltando = (precisa_esquerda and pulsos_esquerda is None) or (
            precisa_direita and pulsos_direita is None
        )
        if faltando:
            # O config promete encoder e a telemetria nao traz o campo. Isso e
            # firmware velho ou nome de campo trocado - avisa UMA vez (a
            # telemetria chega a cada 500 ms) e segue nos passos comandados.
            if not self._avisou_encoder_ausente:
                self._avisou_encoder_ausente = True
                logger.error(
                    "motion.encoder_lado=%r mas a telemetria nao traz "
                    "pulsos_esquerda/pulsos_direita - o firmware publica esses "
                    "campos? Caindo para os passos comandados.",
                    self._encoder_lado,
                )
            return (None, None)
        self._avisou_encoder_ausente = False

        anterior_esquerda = self._pulsos_esquerda_anterior
        anterior_direita = self._pulsos_direita_anterior
        self._pulsos_esquerda_anterior = pulsos_esquerda
        self._pulsos_direita_anterior = pulsos_direita

        primeira_leitura = (precisa_esquerda and anterior_esquerda is None) or (
            precisa_direita and anterior_direita is None
        )
        if primeira_leitura:
            return (None, None)  # so guardou a base; nao ha delta ainda

        delta_esquerda = (pulsos_esquerda - anterior_esquerda) if precisa_esquerda else 0
        delta_direita = (pulsos_direita - anterior_direita) if precisa_direita else 0

        # REINICIO DO HARDWARE CORE: mesma assinatura usada nos passos - os
        # contadores voltam a zero, porque nascem zerados no EncoderManager.
        zerou = (not precisa_esquerda or pulsos_esquerda == 0) and (
            not precisa_direita or pulsos_direita == 0
        )
        if zerou and (delta_esquerda != 0 or delta_direita != 0):
            logger.warning(
                "Contadores do encoder zeraram (delta esq=%d dir=%d) - provavel "
                "reinicio do Hardware Core, ressincronizando sem usar este quadro",
                delta_esquerda,
                delta_direita,
            )
            return (None, None)

        # Sem `odometry_correction_factor` aqui de proposito: aquele fator
        # corrige o CHUTE de passos por metro (Cap 12 s.9). O encoder tem
        # escala propria, medida empurrando o robo - aplicar os dois seria
        # corrigir duas vezes.
        metros_por_pulso = 1.0 / self._encoder_pulsos_por_metro
        return (
            delta_esquerda * metros_por_pulso if precisa_esquerda else None,
            delta_direita * metros_por_pulso if precisa_direita else None,
        )

    def _delta_yaw(self, payload: dict[str, Any]) -> float | None:
        """Quanto o robo girou, em radianos, segundo o giroscopio.

        None quando a telemetria nao traz `yaw_graus` (o caso de hoje) ou
        quando e a primeira leitura e ainda nao ha base de comparacao.
        """
        yaw_graus = payload.get("yaw_graus")
        if yaw_graus is None:
            return None

        yaw_rad = math.radians(float(yaw_graus))
        anterior_rad = self._yaw_anterior_rad
        self._yaw_anterior_rad = yaw_rad
        if anterior_rad is None:
            return None

        # menor arco entre os dois angulos: cruzar de 359 para 1 grau e +2,
        # nao -358. Sem isso, cada volta completa injetaria um salto enorme.
        return (yaw_rad - anterior_rad + math.pi) % (2 * math.pi) - math.pi

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
