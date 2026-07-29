"""Instintos do Fofão: perceber que a própria medida não está confiável.

A ideia é do mantenedor (2026-07-29): *"o robô tem que se calibrar sempre q
precisar corrigir, se seus dados estiverem oscilando"*. Um instinto não decide
o que fazer no mundo - isso é do maestro e da IA. Ele faz uma coisa só, e
antes de todo o resto: **olha para o próprio sensor e diz se dá para confiar.**

POR QUE ISTO EXISTE, com dois casos reais do mesmo dia:

- O fio do servo do radar estava solto. O firmware continuou reportando
  `radar_angulo_graus` com toda a confiança, porque esse valor vem de
  `_anguloAtual` - estado de software atualizado por `_servo.write()`, sem
  nenhuma confirmação física. Sete ângulos cobrindo 120° voltaram todos dentro
  de 2% um do outro, e nada no sistema achou isso estranho.
- A bússola oscila 8 a 12 graus com o robô parado, sem calibração. Ela é
  honesta o bastante para marcar `rumo_valido: false`, mas ninguém mede o
  quanto ela oscila - então não há como perceber a degradação DEPOIS de uma
  calibração bem-sucedida, que é justamente quando o robô passaria a confiar
  nela.

Em ambos, o dado errado não vinha de sensor quebrado: vinha de o sistema não
ter nenhum jeito de dizer "não sei".
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque

from orion.kernel.event_bus import EventBus, Evento, Prioridade

logger = logging.getLogger("motion_core.behavior.instintos")

TOPICO_TELEMETRIA = "comm.mensagem.telemetry"
TOPICO_ALERTA = "diagnostic.alerta"
TOPICO_COMANDO = "navigation.comando"


def dispersao_circular_graus(angulos: list[float]) -> float:
    """Maior desvio angular em relação à direção média, em graus.

    NÃO é `max - min`: rumo é circular, e a conta ingênua quebra no norte -
    359° e 1° estão a 2° de distância, mas a subtração direta dá 358°. Um
    robô olhando para o norte pareceria ter a bússola pior do sistema.

    Média de ângulos também não é a média aritmética: soma-se os vetores
    unitários e tira-se o ângulo da resultante.
    """
    if len(angulos) < 2:
        return 0.0
    soma_x = sum(math.cos(math.radians(a)) for a in angulos)
    soma_y = sum(math.sin(math.radians(a)) for a in angulos)
    if soma_x == 0.0 and soma_y == 0.0:
        # Amostras perfeitamente opostas - não há direção média. Isso é
        # dispersão máxima, não zero.
        return 180.0
    media = math.degrees(math.atan2(soma_y, soma_x))
    maior = 0.0
    for a in angulos:
        diferenca = abs((a - media + 180.0) % 360.0 - 180.0)
        maior = max(maior, diferenca)
    return maior


class InstintoBussola:
    """Percebe rumo oscilando demais e pede calibração - com trava de laço.

    NÃO É UM RECALIBRADOR PERIÓDICO. A calibração da bússola é gravada na
    EEPROM do Mega (`EEPROM_ENDERECO_BUSSOLA = 64`, com número mágico 0x6310
    e checagem de sanidade da escala em `_carregarCalibracao`), então ela
    sobrevive a reset - inclusive aos resets que a serial provoca a cada
    reconexão. Calibra-se UMA vez por montagem física.

    O que este instinto faz é perceber que a calibração **guardada deixou de
    valer**: alguém moveu a bússola, aparafusou algo metálico perto, trocou o
    chassi. Sem ele, uma calibração velha continuaria sendo apresentada como
    boa para sempre, e o robô navegaria por um rumo que já não descreve o
    mundo.

    Só mede com o robô PARADO. Andando, a oscilação do rumo é esperada (o robô
    está de fato girando) e os ímãs permanentes dos motores de passo distorcem
    o campo mesmo desligados - medir aí acusaria problema o tempo todo.

    A TRAVA IMPORTA TANTO QUANTO O LIMIAR. O limiar de 6° foi arbitrado pelo
    mantenedor a partir de metade da oscilação medida hoje sem calibração
    (8-12°). Ele acerta no caso conhecido, mas ninguém sabe ainda se uma
    bússola CALIBRADA nesta montagem consegue ficar abaixo dele - o robô está
    preso a cabos e não pode girar para calibrar de verdade.

    Se não conseguir, sem trava o robô calibraria para sempre: mede 7°,
    calibra, mede 7°, calibra. Com trava, um limiar errado vira só um aviso
    repetido - que é exatamente o que se quer de um palpite ainda não medido.
    """

    #: Quanto a dispersão precisa CAIR abaixo do limiar para o instinto voltar
    #: a dizer "estável". Sem esta folga ele pisca: medido em produção em
    #: 2026-07-29, com o limiar em 6,0° a dispersão real ficou em 5,7 / 6,7 /
    #: 6,0 / 6,1 / 5,7 - alertando e desalertando sem parar. Aviso que pisca
    #: vira ruído que se aprende a ignorar, que é o oposto do que um instinto
    #: deve ser. Mesmo padrão do guardião de RAM (limiar_critico/limiar_folga).
    FOLGA_HISTERESE_GRAUS = 1.5

    def __init__(
        self,
        event_bus: EventBus,
        dispersao_maxima_graus: float,
        amostras: int = 12,
        tentativas_max: int = 2,
        intervalo_minimo_s: float = 600.0,
        pedir_calibracao: bool = False,
        folga_histerese_graus: float | None = None,
    ) -> None:
        self._bus = event_bus
        self._limiar = dispersao_maxima_graus
        self._folga = (
            folga_histerese_graus
            if folga_histerese_graus is not None
            else self.FOLGA_HISTERESE_GRAUS
        )
        self._amostras: deque[float] = deque(maxlen=amostras)
        self._minimo = amostras
        self._tentativas_max = tentativas_max
        self._intervalo_minimo_s = intervalo_minimo_s
        #: Desligado por padrão: pedir calibração faz o robô GIRAR 25s sozinho.
        #: Enquanto não houver espaço confirmado em volta, o instinto só avisa.
        self._pedir_calibracao = pedir_calibracao
        self._tentativas = 0
        self._ultima_tentativa = 0.0
        self._alertado = False

        event_bus.subscribe(TOPICO_TELEMETRIA, self._ao_receber_telemetria)

    async def _ao_receber_telemetria(self, evento: Evento) -> None:
        payload = evento.dados.get("payload") or {}

        # Só mede parado - ver docstring. Movimento zera a janela em vez de
        # misturar amostras de antes e depois, que produziria uma dispersão
        # inventada (o robô girou de verdade no meio).
        if payload.get("em_movimento"):
            self._amostras.clear()
            return

        rumo = payload.get("rumo_graus")
        if rumo is None:
            return
        self._amostras.append(float(rumo))
        if len(self._amostras) < self._minimo:
            return

        dispersao = dispersao_circular_graus(list(self._amostras))

        # HISTERESE: sobe no limiar, só desce depois de melhorar de verdade.
        # Enquanto alertado, exige `limiar - folga` para declarar estabilidade -
        # senão uma dispersão que oscila em cima do limiar (medido: 5,7 / 6,7 /
        # 6,0 / 6,1) faz o instinto piscar.
        limite_para_voltar = self._limiar - self._folga
        if self._alertado:
            if dispersao <= limite_para_voltar:
                logger.info(
                    "bússola voltou a ficar estável (%.1f°, abaixo de %.1f°)",
                    dispersao,
                    limite_para_voltar,
                )
                self._alertado = False
                self._tentativas = 0
                return
            # Continua ruim. Não repete o alerta (`_reagir` só publica quando
            # ainda não alertou), mas segue avaliando - senão a retentativa de
            # calibração morreria aqui, e `tentativas_max` nunca passaria de 1.
            await self._reagir(dispersao, payload)
            return

        if dispersao <= self._limiar:
            self._tentativas = 0
            return

        await self._reagir(dispersao, payload)

    async def _reagir(self, dispersao: float, payload: dict) -> None:
        if not self._alertado:
            logger.warning(
                "instinto: rumo oscilando %.1f° com o robô parado (limiar %.1f°) - "
                "não dá para confiar nesta bússola",
                dispersao,
                self._limiar,
            )
            await self._bus.publish(
                TOPICO_ALERTA,
                {
                    "origem": "instinto_bussola",
                    "tipo": "rumo_instavel",
                    "dispersao_graus": round(dispersao, 2),
                    "limiar_graus": self._limiar,
                    "calibrada": payload.get("bussola_calibrada"),
                },
                prioridade=Prioridade.ALTA,
            )
            self._alertado = True

        if not self._pedir_calibracao:
            return
        agora = time.monotonic()
        if self._tentativas >= self._tentativas_max:
            return  # já tentou o bastante - avisar é melhor que girar em laço
        if self._ultima_tentativa and agora - self._ultima_tentativa < self._intervalo_minimo_s:
            return
        self._ultima_tentativa = agora
        self._tentativas += 1
        logger.warning(
            "instinto: pedindo calibração da bússola (tentativa %d de %d)",
            self._tentativas,
            self._tentativas_max,
        )
        # Pelo Event Bus e como comando de navegação, nunca direto ao Mega
        # (regra 1 do ARQUITETURA.txt, regra 2 do contrato da IA).
        await self._bus.publish(
            TOPICO_COMANDO, {"acao": "MANUAL", "comando": "CALIBRATE_COMPASS"}
        )

    @property
    def dispersao_atual_graus(self) -> float | None:
        """Dispersão da janela cheia, ou None se ainda não há amostras bastantes."""
        if len(self._amostras) < self._minimo:
            return None
        return dispersao_circular_graus(list(self._amostras))
