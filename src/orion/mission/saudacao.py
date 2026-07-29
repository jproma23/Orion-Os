"""Saudação espontânea: o Fofão fala primeiro ao reconhecer alguém.

Pedido do dono (2026-07-29): *"quero que ele fale tudo"*, *"praticamente ao me
ver me cumprimentar"*, *"agir espontaneamente como estivesse vendo o seu
projetista e criador"*.

POR QUE ISTO SÓ FOI POSSÍVEL HOJE: até esta data o robô não tinha como saber
QUEM estava na frente dele. O `vision.person_recognized` - o único evento que
carrega o nome - era publicado no Notebook e não chegava a lugar nenhum: o
diário lia o nome do evento errado, o `EstadoDoMundo` também, e o FOLLOW
esperava por ele sem que ninguém o encaminhasse. Ele via o dono todos os dias
e registrava "vi uma pessoa que não reconheci". Cumprimentar era impossível
por falta de identidade, não por falta de voz.

O CUIDADO QUE DEFINE ESTE MÓDULO: `vision.person_recognized` sai uma vez por
QUADRO rastreado - até 15 vezes por segundo. Falar a cada um seria um robô
gritando o seu nome sem parar. Aqui, reconhecer é diferente de CHEGAR: só a
chegada merece um cumprimento.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Awaitable, Callable

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("orion.mission.saudacao")

TOPICO_RECONHECIDA = "vision.person_recognized"
TOPICO_PERDIDA = "vision.person_lost"
TOPICO_ACORDOU = "voice.wake_detected"
TOPICO_RESPOSTA_FIM = "voice.response_finished"
TOPICO_SAUDACAO = "voice.saudacao"

#: Tempo sem ver a pessoa para que revê-la conte como CHEGADA de novo.
#: Meia hora: sair da sala e voltar em dois minutos não é chegar - e um robô
#: que cumprimenta a cada passagem cansa em uma tarde.
INTERVALO_ENTRE_SAUDACOES_S = 1800.0

#: Variações para não soar gravado. A escolha é aleatória de propósito: a
#: mesma frase toda vez faz o robô parecer um alarme, não um morador.
FRASES = (
    "Oi, {nome}!",
    "Olá, {nome}! Que bom te ver.",
    "{nome}! Você por aqui.",
    "Oi, {nome}, tudo bem?",
    "Opa, {nome}! Bom te ver de novo.",
)

#: Cumprimento por RELAÇÃO, definida pelo dono em `config/orion.yaml`
#: (seção `familia`). Ele não trata todo mundo igual porque as pessoas da casa
#: não são iguais para ele: um é quem o construiu, outro é uma criança de 3
#: anos, outra é quem cuida da casa.
#:
#: Relação sem entrada aqui cai em FRASES - genérico funciona, só é menos
#: pessoal. Nunca falha por falta de correspondência.
FRASES_POR_RELACAO = {
    "criador": (
        "Oi, {nome}! Que bom te ver, meu criador.",
        "{nome}! Você voltou. Estava aqui de olho na casa.",
        "Olá, {nome}! Pronto para o que você precisar.",
    ),
    "irmao": (
        "Oi, {nome}!",
        "{nome}! Vamos brincar?",
        "Olha o {nome} chegando!",
    ),
    "mae": (
        "Olá, {nome}!",
        "Oi, {nome}, tudo bem por aí?",
        "{nome}! Está tudo em ordem por aqui.",
    ),
}


class SaudacaoEspontanea:
    """Cumprimenta pelo nome quando alguém conhecido CHEGA.

    Roda no Notebook, junto com a voz e a visão - é lá que estão o
    reconhecimento facial e o sintetizador.
    """

    def __init__(
        self,
        event_bus: EventBus,
        falar: Callable[[str], Awaitable[None]],
        intervalo_entre_saudacoes_s: float = INTERVALO_ENTRE_SAUDACOES_S,
        agora: Callable[[], float] | None = None,
        escolher_frase: Callable[[tuple[str, ...]], str] | None = None,
        familia: list[dict] | None = None,
    ) -> None:
        self._bus = event_bus
        self._falar = falar
        #: nome -> relação, vinda do orion.yaml. Nome fora daqui recebe o
        #: cumprimento genérico em vez de nenhum.
        self._relacao_de = {
            str(p.get("nome")): str(p.get("relacao", "")) for p in (familia or [])
        }
        self._intervalo_s = intervalo_entre_saudacoes_s
        self._agora = agora or time.monotonic
        self._escolher = escolher_frase or random.choice
        #: nome -> instante do último cumprimento
        self._cumprimentados: dict[str, float] = {}
        #: True entre "Fofão" e o fim da resposta - não se fala por cima de
        #: uma conversa em andamento. Cumprimentar alguém no meio da própria
        #: frase é pior do que não cumprimentar.
        self._conversando = False

        event_bus.subscribe(TOPICO_RECONHECIDA, self._ao_reconhecer)
        event_bus.subscribe(TOPICO_ACORDOU, self._ao_acordar)
        event_bus.subscribe(TOPICO_RESPOSTA_FIM, self._ao_terminar_resposta)

    async def _ao_acordar(self, evento: Evento) -> None:
        self._conversando = True

    async def _ao_terminar_resposta(self, evento: Evento) -> None:
        self._conversando = False

    async def _ao_reconhecer(self, evento: Evento) -> None:
        nome = evento.dados.get("nome")
        if not nome:
            return  # sem identidade não há a quem cumprimentar pelo nome
        if self._conversando:
            return
        if not self._chegou_agora(nome):
            return

        relacao = self._relacao_de.get(nome, "")
        frases = FRASES_POR_RELACAO.get(relacao, FRASES)
        frase = self._escolher(frases).format(nome=nome)
        logger.info("saudação espontânea para %s: %r", nome, frase)
        # Publica ANTES de falar: o avatar reage ao evento, e a fala bloqueia
        # por segundos. Se publicasse depois, a legenda apareceria quando a
        # voz já tivesse terminado.
        await self._bus.publish(TOPICO_SAUDACAO, {"nome": nome, "texto": frase})
        try:
            await self._falar(frase)
        except Exception:
            # Falha na voz não pode derrubar a visão, que é quem chamou este
            # handler (Cap 6 s.8). Um cumprimento perdido é irrelevante perto
            # de perder o rastreamento.
            logger.warning("não consegui falar o cumprimento para %s", nome, exc_info=True)

    def _chegou_agora(self, nome: str) -> bool:
        """True só quando revê-la conta como uma CHEGADA, não continuação.

        Este é o coração do módulo: `vision.person_recognized` chega até 15
        vezes por segundo enquanto a pessoa estiver no quadro. Sem esta janela
        o robô repetiria o nome sem parar.
        """
        agora = self._agora()
        anterior = self._cumprimentados.get(nome)
        if anterior is not None and agora - anterior < self._intervalo_s:
            return False
        self._cumprimentados[nome] = agora
        return True
