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

BLOQUEIA POR PADRAO (EDR-0037, 01/11 a tarde). De manha ela foi posta em
"so informa" (a IA desvia). A tarde, com ela assim, o robo bateu na comoda e
na parede: o LiDAR mediu 34 e 25 cm, ela avisou no log e deixou passar.
Decisao do dono: 50 cm na frente, 50 cm atras, e no MANUAL um piso duro de
15 cm nos dois sentidos (o operador manda, mas nao encosta). Desviar e
trabalho do desvio local do EXPLORE; esta trava e a rede por baixo dele.

WATCHDOG: andando e sem varredura nova ha mais de `idade_max_s`, manda STOP
(`checar_watchdog`, chamado em laco por `vigiar`). Antes so o `lidar.error`
(3 s de silencio) parava um movimento em curso.

O MOVIMENTO EM CURSO ACABA quando chega um giro, um STOP, um CMD_VEL sem
avanco, ou quando a telemetria diz que o corpo parou (depois de um tempo do
comando, para nao confundir "ainda nao comecou" com "ja acabou"). Antes so
STOP/CMD_VEL o encerravam: depois de um MOVE_DISTANCE para a frente, um giro
perto da parede seria parado pela trava.

O PROPRIO CORPO NAO E OBSTACULO (2026-11-08). O LiDAR fica 8 cm atras da
ponta da frente (`chassi.lidar_recuo_m`) e 22 cm a frente da traseira. Um
ponto com avanco menor que isso, dentro do corredor, esta DENTRO da carcaca
ou ao lado dela - nunca a frente. Medido em 08/11: 75 paradas em uma semana
por "obstaculo a 1 cm para frente", o robo so girava no lugar. `corpo_*_cm`
descarta esses pontos.

FRENTE CEGA NO MEIO DO AVANCO pede `cegas_para_parar` varreduras seguidas
(2 = ~0,4 s, ~3 cm a 30%). Uma varredura cega solta no arranque parava o
robo (13 paradas em 08/11). Para SAIR andando continua valendo uma so.

LIMITE FISICO QUE ELA NAO RESOLVE: o LiDAR varre um plano a
`lidar_altura_m` do chao. O que for mais baixo (pe de cama, caixa,
brinquedo) ele nao ve, e esta trava tambem nao.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from motion_core.navigation.desvio_local import FRACAO_CEGA_MAXIMA, fracao_sem_eco
from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("motion_core.navigation.trava_lidar")

FRENTE = "frente"
TRAS = "tras"

#: Angulo (convencao do projeto: 0 esquerda, 90 frente, 180 direita) para
#: onde aponta cada sentido de movimento.
ANGULO_DO_SENTIDO = {FRENTE: 90.0, TRAS: 270.0}

#: Abaixo disto a velocidade linear do CMD_VEL conta como parado (giro puro).
VELOCIDADE_PARADO_M_S = 0.01

#: Comandos que giram no lugar: encerram o avanco em curso.
COMANDOS_DE_GIRO = frozenset({"TURN_LEFT", "TURN_RIGHT"})
#: Telemetria "parado" so encerra o movimento depois deste tempo do comando -
#: logo apos o envio o corpo ainda nao comecou a andar.
TELEMETRIA_PARADO_APOS_S = 1.0
#: Intervalo do laco do watchdog.
INTERVALO_WATCHDOG_S = 0.1


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
        distancia_cm: float = 50.0,
        meia_largura_cm: float = 25.0,
        idade_max_s: float = 0.6,
        relogio=time.monotonic,
        bloquear: bool = True,
        piso_manual_cm: float = 15.0,
        corpo_frente_cm: float = 0.0,
        corpo_tras_cm: float = 0.0,
        cegas_para_parar: int = 1,
    ) -> None:
        self._bloquear = bloquear
        #: Do LiDAR ate a ponta do corpo em cada sentido: ponto com avanco
        #: menor que isto e o proprio robo (ou esta ao lado dele).
        self._corpo_cm = {FRENTE: corpo_frente_cm, TRAS: corpo_tras_cm}
        self._cegas_para_parar = max(1, int(cegas_para_parar))
        self._cegas_seguidas = 0
        self._distancia_cm = distancia_cm
        self._piso_manual_cm = piso_manual_cm
        #: Modo de controle do maestro ("autonomo" / "manual"). No manual vale
        #: o piso, nao o limite cheio.
        self._modo_controle = "autonomo"
        self._comando_em_curso_em: float | None = None
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
        event_bus.subscribe("behavior.modo_controle", self._ao_mudar_modo)
        event_bus.subscribe("comm.mensagem.telemetry", self._ao_receber_telemetria)

    def ligar_parada(self, parar) -> None:
        """`parar`: coroutine sem argumentos que manda STOP ao Hardware Core."""
        self._parar = parar

    # --- o porteiro -------------------------------------------------------

    def motivo_recusa(self, payload: dict[str, Any]) -> str | None:
        """None se o comando pode seguir; senao, o motivo da recusa."""
        comando = payload.get("comando")
        sentido = sentido_do_comando(payload)
        if sentido is None:
            if comando in ("STOP", "CMD_VEL") or comando in COMANDOS_DE_GIRO:
                # STOP, giro, ou CMD_VEL de giro puro / zero: o avanco acabou.
                self._sentido_em_curso = None
            return None
        motivo = self._bloqueio(sentido)
        if motivo is None:
            self._marcar_em_curso(sentido)
            return None
        if not self._bloquear:
            logger.warning("LiDAR avisa (sem bloquear) em %s: %s", comando, motivo)
            self._marcar_em_curso(sentido)
            return None
        logger.warning("trava do LiDAR recusou %s: %s", comando, motivo)
        return motivo

    def _marcar_em_curso(self, sentido: str) -> None:
        self._sentido_em_curso = sentido
        self._comando_em_curso_em = self._relogio()
        self._cegas_seguidas = 0

    @property
    def limite_cm(self) -> float:
        """O limite que vale agora: o piso no manual, o cheio no autonomo."""
        return self._piso_manual_cm if self._modo_controle == "manual" else self._distancia_cm

    def _bloqueio(self, sentido: str) -> str | None:
        if self._raios_cm is None or self._recebido_em is None:
            return "LiDAR sem leitura - nao ando as cegas"
        idade = self._relogio() - self._recebido_em
        if idade > self._idade_max_s:
            return f"leitura do LiDAR velha ({idade:.1f}s) - nao ando as cegas"
        if sentido == FRENTE:
            # Frente cega e obstaculo: o sensor perde a parede perto e de
            # frente (ver desvio_local.FRACAO_CEGA_MAXIMA). So o AVANCO; re e
            # giro seguem livres.
            fracao = fracao_sem_eco(self._raios_cm, ANGULO_DO_SENTIDO[FRENTE])
            if fracao > FRACAO_CEGA_MAXIMA:
                return f"frente cega ({fracao:.0%} dos raios sem eco) - pode ser parede perto"
        perto = self._mais_perto_no_corredor(sentido)
        limite = self.limite_cm
        if perto is not None and perto < limite:
            return f"obstaculo a {perto:.0f} cm para {sentido} (limite {limite:.0f} cm)"
        return None

    async def checar_watchdog(self) -> None:
        """Andando e o LiDAR calado alem de `idade_max_s`: STOP."""
        if self._sentido_em_curso is None or not self._bloquear or self._parar is None:
            return
        if self._recebido_em is None:
            idade = None
        else:
            idade = self._relogio() - self._recebido_em
        if idade is not None and idade <= self._idade_max_s:
            return
        logger.warning(
            "trava do LiDAR PAROU o movimento em curso: sem varredura ha %s",
            "tempo indeterminado" if idade is None else f"{idade:.1f}s",
        )
        self._sentido_em_curso = None
        await self._parar()

    async def vigiar(self) -> None:
        """Laco do watchdog - rodar como task durante a vida do Motion Core."""
        while True:
            try:
                await self.checar_watchdog()
            except Exception:
                logger.exception("trava do LiDAR: watchdog falhou - seguindo")
            await asyncio.sleep(INTERVALO_WATCHDOG_S)

    def _mais_perto_no_corredor(self, sentido: str) -> float | None:
        """Menor avanco (cm, ao longo do movimento) ate um ponto dentro do
        corredor do robo. None se o corredor esta livre ate onde ele mede."""
        eixo = ANGULO_DO_SENTIDO[sentido]
        corpo = self._corpo_cm[sentido]
        menor: float | None = None
        for angulo, distancia in enumerate(self._raios_cm or ()):
            if distancia is None:
                continue
            desvio = math.radians(angulo - eixo)
            avanco = distancia * math.cos(desvio)
            lateral = abs(distancia * math.sin(desvio))
            if avanco > corpo and lateral < self._meia_largura_cm:
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
        if motivo is not None and motivo.startswith("frente cega"):
            self._cegas_seguidas += 1
            if self._cegas_seguidas < self._cegas_para_parar:
                return
        else:
            self._cegas_seguidas = 0
        if motivo is not None and self._parar is not None and self._bloquear:
            logger.warning("trava do LiDAR PAROU o movimento em curso: %s", motivo)
            self._sentido_em_curso = None
            await self._parar()

    async def _ao_mudar_modo(self, evento: Evento) -> None:
        modo = evento.dados.get("modo")
        if modo:
            self._modo_controle = str(modo)

    async def _ao_receber_telemetria(self, evento: Evento) -> None:
        """Corpo parado (depois de dar tempo ao comando) = avanco acabou."""
        if self._sentido_em_curso is None or self._comando_em_curso_em is None:
            return
        payload = evento.dados.get("payload") or evento.dados
        if payload.get("em_movimento") is not False:
            return
        if self._relogio() - self._comando_em_curso_em < TELEMETRIA_PARADO_APOS_S:
            return
        self._sentido_em_curso = None

    async def _ao_falhar(self, evento: Evento) -> None:
        self._raios_cm = None
        self._recebido_em = None
        if self._sentido_em_curso is not None and self._parar is not None and self._bloquear:
            logger.warning("trava do LiDAR PAROU o movimento em curso: o LiDAR falhou")
            self._sentido_em_curso = None
            await self._parar()
