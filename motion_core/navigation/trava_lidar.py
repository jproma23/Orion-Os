"""Trava do LiDAR: nenhum comando de roda anda para cima de obstaculo (2026-11-01).

POR QUE EXISTE. A checagem de obstaculo morava no NavigationCore - e so
valia para o que passasse por ele. A acao falada do Notebook ia direto ao
Hardware Core pelo roteamento transparente do Pi (`_encaminhar`), sem olhar
o LiDAR. Medido em 01/11: o robo conversou com o proprio eco, a IA escolheu
`andar_para_frente`, o MOVE_FORWARD chegou nas rodas com o LiDAR lendo 312
de 360 raios - ele via, so que quem mandou andar nao olhou - e bateu.

Decisao do dono no mesmo dia (opcao "a"): trava de verdade, venha de quem
vier. Por isso ela e o PORTEIRO do enlace `hardware_core` no
ComunicacaoService - o unico ponto por onde passam TODOS os comandos de
roda: os nascidos no Pi (webui, maestro, Nav2) e os repassados do Notebook.

O QUE ELA CONFERE: o CORREDOR que o robo vai ocupar, nao um cone. Um ponto
do LiDAR so bloqueia se estiver na direcao do movimento, dentro da largura
do robo (mais folga) e mais perto que `distancia_cm`. Parede ao lado num
corredor nao trava; caixa na frente, sim. Giro no lugar sempre passa.

"NAO SEI" NAO E CAMINHO LIVRE: sem varredura recente, comando que anda e
recusado. E se o obstaculo aparece DURANTE um movimento continuo (CMD_VEL,
MOVE_CONTINUOUS), recusar o proximo comando nao basta - ela manda STOP.

INFORMA, NAO BLOQUEIA - POR PADRAO (mesmo dia, decisao do dono: "nada tem
que ser bloqueante, mas informar os obstaculos; a IA desvia"). E a regra de
julho ("sem travas, com estudo de risco") reafirmada, agora que o retrato
da IA traz os quatro lados do LiDAR. Com `bloquear=False` o comando sempre
passa e o obstaculo vira aviso no log; o modo que recusa e para continua
aqui e volta com `motion.trava_lidar.bloquear: true`.

LIMITE FISICO QUE ELA NAO RESOLVE: o LiDAR varre um plano a
`lidar_altura_m` do chao. O que for mais baixo (pe de cama, caixa,
brinquedo) ele nao ve, e esta trava tambem nao.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("motion_core.navigation.trava_lidar")

FRENTE = "frente"
TRAS = "tras"

#: Angulo (convencao do projeto: 0 esquerda, 90 frente, 180 direita) para
#: onde aponta cada sentido de movimento.
ANGULO_DO_SENTIDO = {FRENTE: 90.0, TRAS: 270.0}

#: Abaixo disto a velocidade linear do CMD_VEL conta como parado (giro puro).
VELOCIDADE_PARADO_M_S = 0.01


def sentido_do_comando(payload: dict[str, Any]) -> str | None:
    """Para onde o comando leva o robo: FRENTE, TRAS ou None (nao anda)."""
    comando = payload.get("comando")
    if comando in ("MOVE_FORWARD", "MOVE_CONTINUOUS"):
        return FRENTE
    if comando == "MOVE_DISTANCE":
        distancia = float(payload.get("distancia_cm", 0.0) or 0.0)
        return FRENTE if distancia > 0 else TRAS if distancia < 0 else None
    if comando == "CMD_VEL":
        v = float(payload.get("v_m_s", 0.0) or 0.0)
        if v > VELOCIDADE_PARADO_M_S:
            return FRENTE
        if v < -VELOCIDADE_PARADO_M_S:
            return TRAS
    return None


class TravaLidar:
    def __init__(
        self,
        event_bus: EventBus,
        distancia_cm: float = 35.0,
        meia_largura_cm: float = 25.0,
        idade_max_s: float = 1.5,
        relogio=time.monotonic,
        bloquear: bool = False,
    ) -> None:
        self._bloquear = bloquear
        self._distancia_cm = distancia_cm
        self._meia_largura_cm = meia_largura_cm
        self._idade_max_s = idade_max_s
        self._relogio = relogio
        self._raios_cm: list[float | None] | None = None
        self._recebido_em: float | None = None
        #: Sentido do ultimo comando que ANDA e passou. Serve para parar um
        #: movimento continuo quando o obstaculo aparece no meio dele.
        self._sentido_em_curso: str | None = None
        #: Quem manda o STOP (injetado depois, porque o comm nasce antes).
        self._parar = None
        event_bus.subscribe("lidar.scan", self._ao_receber_scan)
        event_bus.subscribe("lidar.error", self._ao_falhar)

    def ligar_parada(self, parar) -> None:
        """`parar`: coroutine sem argumentos que manda STOP ao Hardware Core."""
        self._parar = parar

    # --- o porteiro -------------------------------------------------------

    def motivo_recusa(self, payload: dict[str, Any]) -> str | None:
        """None se o comando pode seguir; senao, o motivo da recusa."""
        comando = payload.get("comando")
        sentido = sentido_do_comando(payload)
        if sentido is None:
            if comando in ("STOP", "CMD_VEL"):
                # STOP, ou CMD_VEL de giro puro / zero: nada mais em curso.
                self._sentido_em_curso = None
            return None
        motivo = self._bloqueio(sentido)
        if motivo is None:
            self._sentido_em_curso = sentido
            return None
        if not self._bloquear:
            # So informa: quem decide desviar e a IA, que ve os 4 lados.
            logger.warning("LiDAR avisa (sem bloquear) em %s: %s", comando, motivo)
            self._sentido_em_curso = sentido
            return None
        logger.warning("trava do LiDAR recusou %s: %s", comando, motivo)
        return motivo

    def _bloqueio(self, sentido: str) -> str | None:
        if self._raios_cm is None or self._recebido_em is None:
            return "LiDAR sem leitura - nao ando as cegas"
        idade = self._relogio() - self._recebido_em
        if idade > self._idade_max_s:
            return f"leitura do LiDAR velha ({idade:.1f}s) - nao ando as cegas"
        perto = self._mais_perto_no_corredor(sentido)
        if perto is not None and perto < self._distancia_cm:
            return f"obstaculo a {perto:.0f} cm para {sentido} (limite {self._distancia_cm:.0f} cm)"
        return None

    def _mais_perto_no_corredor(self, sentido: str) -> float | None:
        """Menor avanco (cm, ao longo do movimento) ate um ponto dentro do
        corredor do robo. None se o corredor esta livre ate onde ele mede."""
        eixo = ANGULO_DO_SENTIDO[sentido]
        menor: float | None = None
        for angulo, distancia in enumerate(self._raios_cm or ()):
            if distancia is None:
                continue
            desvio = math.radians(angulo - eixo)
            avanco = distancia * math.cos(desvio)
            lateral = abs(distancia * math.sin(desvio))
            if avanco > 0 and lateral < self._meia_largura_cm:
                if menor is None or avanco < menor:
                    menor = avanco
        return menor

    # --- o que ela escuta -------------------------------------------------

    async def _ao_receber_scan(self, evento: Evento) -> None:
        raios = evento.dados.get("raios_cm")
        if not raios or len(raios) != 360:
            return
        self._raios_cm = list(raios)
        self._recebido_em = self._relogio()
        sentido = self._sentido_em_curso
        if sentido is None:
            return
        motivo = self._bloqueio(sentido)
        if motivo is not None and self._parar is not None and self._bloquear:
            logger.warning("trava do LiDAR PAROU o movimento em curso: %s", motivo)
            self._sentido_em_curso = None
            await self._parar()

    async def _ao_falhar(self, evento: Evento) -> None:
        self._raios_cm = None
        self._recebido_em = None
        if self._sentido_em_curso is not None and self._parar is not None and self._bloquear:
            logger.warning("trava do LiDAR PAROU o movimento em curso: o LiDAR falhou")
            self._sentido_em_curso = None
            await self._parar()
