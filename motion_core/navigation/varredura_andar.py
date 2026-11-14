"""Modo varredura ao andar: a cabeca olha o CHAO enquanto o robo anda.

Pedido do dono (2026-11-14): *"a ia precisa receber frame quando o robo anda
para ver se vai bater em algo no chao pq o lidar esta em cima"*, *"usando o
ptz da camera vamos mandar frame para ia quando ver obstaculos se
aproximando em sua direcao"*, *"modo varredura ao andar para auxiliar"*.

O LiDAR fica a ~45 cm do chao: tapete enrolado, brinquedo, fio, pe de
cadeira passam POR BAIXO do feixe. A camera da cabeca e o unico sensor que
pode ve-los - desde que esteja olhando para baixo.

O que este modulo faz (so isso - quem VE e quem DECIDE moram no Notebook):

1. pergunta a trava do LiDAR se ha movimento PARA A FRENTE em curso (ela ve
   todo comando de roda que sai para o corpo e encerra o movimento pela
   telemetria - nao ha segundo rastreador para divergir);
2. andando, abaixa a cabeca e varre: centro, esquerda, centro, direita - o
   centro aparece no dobro das vezes porque e por onde o robo vai passar;
3. avisa o Notebook (`vision.varredura_chao` {ativo}) para a visao de la
   ligar o detector de chao e PARAR de mexer na cabeca;
4. parou, espera um respiro (o Nav2 pausa entre um alvo e outro) e devolve a
   cabeca ao centro.

FOLLOW fica de fora: ali a cabeca e da pessoa que ele segue.

Enquanto a varredura esta ativa, o `__main__` ignora o pan/tilt dos
comportamentos (`ativa`): dois donos da cabeca ao mesmo tempo e o defeito
mais antigo deste projeto ("os servos estao loucos", 03/08).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from motion_core.navigation.trava_lidar import FRENTE
from orion.kernel.event_bus import EventBus

logger = logging.getLogger("motion_core.navigation.varredura_andar")

ORIGEM_VARREDURA = "varredura"
TOPICO_VARREDURA = "vision.varredura_chao"

#: Cabeca para baixo: -30 e o batente do tilt (orion.yaml); -25 deixa folga.
TILT_CHAO_GRAUS = -25.0
#: Centro, esquerda, centro, direita. 30 graus: a ~60-100 cm, cobre a largura
#: do corpo (39 cm) com sobra para cada lado.
SEQUENCIA_PAN_GRAUS = (0.0, -30.0, 0.0, 30.0)
#: Quanto a cabeca fica em cada posicao. A visao processa 2 quadros/s e o
#: servo anda 60 graus/s (30 graus = 0,5 s): 0,9 s garante ao menos um quadro
#: PARADO em cada ponto. Ciclo de 3,6 s; o centro a cada 1,8 s = ~20 cm a
#: 0,11 m/s.
PERMANENCIA_S = 0.9
#: Parado ha menos que isto ainda conta como andando: entre dois alvos do
#: Nav2 e entre dois passos de MOVE_DISTANCE a cabeca nao sobe e desce.
RESPIRO_S = 2.0
INTERVALO_S = 0.1


class VarreduraAoAndar:
    def __init__(
        self,
        event_bus: EventBus,
        trava: Any,
        navigation: Any,
        comm: Any | None = None,
        relogio=None,
    ) -> None:
        self._event_bus = event_bus
        self._trava = trava
        self._navigation = navigation
        self._comm = comm
        self._relogio = relogio or (lambda: asyncio.get_running_loop().time())
        self._ativa = False
        self._andou_em: float | None = None
        self._indice = 0
        self._proxima_posicao_em = 0.0

    @property
    def ativa(self) -> bool:
        return self._ativa

    def _andando(self) -> bool:
        if self._trava.sentido_em_curso != FRENTE:
            return False
        modo = getattr(self._navigation, "modo", None)
        return getattr(modo, "value", modo) != "FOLLOW"

    async def passo(self) -> None:
        """Uma iteracao do laco - separada para os testes."""
        agora = self._relogio()
        if self._andando():
            self._andou_em = agora
            if not self._ativa:
                await self._ligar()
            if agora >= self._proxima_posicao_em:
                pan = SEQUENCIA_PAN_GRAUS[self._indice % len(SEQUENCIA_PAN_GRAUS)]
                self._indice += 1
                self._proxima_posicao_em = agora + PERMANENCIA_S
                await self._mover(pan, TILT_CHAO_GRAUS)
        elif self._ativa and (self._andou_em is None or agora - self._andou_em >= RESPIRO_S):
            await self._desligar()

    async def iniciar(self) -> None:
        while True:
            try:
                await self.passo()
            except Exception:
                # Varredura e auxilio: um erro aqui nao pode derrubar o Motion Core.
                logger.exception("varredura ao andar: passo falhou")
            await asyncio.sleep(INTERVALO_S)

    async def _ligar(self) -> None:
        self._ativa = True
        self._indice = 0
        self._proxima_posicao_em = 0.0
        logger.info("varredura ao andar: LIGADA - cabeca olhando o chao")
        await self._avisar_notebook(True)

    async def _desligar(self) -> None:
        self._ativa = False
        logger.info("varredura ao andar: desligada - cabeca ao centro")
        await self._avisar_notebook(False)
        await self._mover(0.0, 0.0)

    async def _mover(self, pan: float, tilt: float) -> None:
        await self._event_bus.publish(
            "motion.pan_tilt", {"pan": pan, "tilt": tilt, "origem": ORIGEM_VARREDURA}
        )

    async def _avisar_notebook(self, ativo: bool) -> None:
        dados = {"ativo": ativo, "tilt": TILT_CHAO_GRAUS}
        # Local tambem: a webui mostra, e quem estiver no Pi sabe.
        await self._event_bus.publish(TOPICO_VARREDURA, dict(dados))
        if self._comm is None:
            return
        try:
            await self._comm.publish(TOPICO_VARREDURA, dados, local=False)
        except Exception:
            # Notebook fora: a cabeca varre do mesmo jeito; so nao ha quem olhe.
            logger.debug("varredura: aviso ao Notebook nao chegou", exc_info=True)
