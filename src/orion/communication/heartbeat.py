"""Heartbeat entre enlaces (Cap 14 secao 6).

Envia HEARTBEAT periodicamente a cada peer monitorado e observa os que
chegam. 3 heartbeats perdidos (configuravel) -> comm.module_lost; quando um
heartbeat volta a chegar de um peer marcado como perdido -> comm.module_recovered.
Reutiliza o HealthMonitor do Kernel (Cap 6) para nao duplicar a logica de
rastreamento de heartbeats.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus, Evento, Prioridade
from orion.kernel.watchdog import HealthMonitor

logger = logging.getLogger("orion.communication.heartbeat")

CallbackReconexao = Callable[[], "Awaitable[None]"]


class MonitorHeartbeat:
    def __init__(
        self,
        servico: ComunicacaoService,
        event_bus: EventBus,
        intervalo_s: float = 1.0,
        heartbeats_perdidos_limite: int = 3,
    ) -> None:
        self._servico = servico
        self._event_bus = event_bus
        self._intervalo_s = intervalo_s
        self._health_monitor = HealthMonitor(
            intervalo_heartbeat_s=intervalo_s,
            heartbeats_perdidos_limite=heartbeats_perdidos_limite,
        )
        self._peers: list[str] = []
        self._reconectar: dict[str, CallbackReconexao | None] = {}
        self._perdidos_atualmente: set[str] = set()
        #: Peers com quem a comunicacao JA funcionou pelo menos uma vez.
        #: Serve para separar "ainda nao conectei" de "perdi a conexao" -
        #: ver _marcar_perdido.
        self._ja_estabelecidos: set[str] = set()
        #: Falhas de envio seguidas, por peer (zera a cada sucesso). Da a
        #: quem NUNCA conectou uma carencia antes de virar perda - ver
        #: _marcar_perdido.
        self._falhas_envio: dict[str, int] = {}
        #: Limite de perdidos efetivo de cada peer (o global, ou o proprio
        #: se `monitorar` recebeu um).
        self._limite_perdidos: dict[str, int] = {}
        self._limite_global = heartbeats_perdidos_limite
        self._executando = False

        event_bus.subscribe("comm.mensagem.heartbeat", self._ao_receber_heartbeat)

    def monitorar(
        self,
        nome_peer: str,
        reconectar: CallbackReconexao | None = None,
        heartbeats_perdidos_limite: int | None = None,
    ) -> None:
        """Passa a rastrear o heartbeat de `nome_peer`. `reconectar`, se
        fornecido, e chamado (best-effort) quando o heartbeat se perde.

        `heartbeats_perdidos_limite` da a ESTE enlace uma paciencia
        diferente da global (None = herda). Serve para nao tratar todos os
        links igual: o Arduino precisa de deteccao rapida, o Notebook
        precisa aguentar pausas longas de CPU. Ver HealthMonitor.timeout_de.
        """
        # Idempotente de proposito: `monitorar` e chamado do callback de
        # conexao TCP, que roda de novo a CADA reconexao do peer. Sem esta
        # guarda o mesmo nome entrava varias vezes em _peers e o laco
        # passava a mandar 1 heartbeat por copia (e a cobrar perda varias
        # vezes) - achado real de 2026-07-25, com o link caindo e voltando.
        ja_monitorado = nome_peer in self._peers
        if not ja_monitorado:
            self._peers.append(nome_peer)

        self._reconectar[nome_peer] = reconectar
        self._limite_perdidos[nome_peer] = (
            heartbeats_perdidos_limite
            if heartbeats_perdidos_limite is not None
            else self._limite_global
        )
        # Re-registrar zera o relogio do peer no HealthMonitor, que e
        # justamente o que se quer numa reconexao: a contagem recomeca do
        # zero em vez de carregar o atraso de quando o link estava caido.
        self._health_monitor.registrar_modulo(
            nome_peer, heartbeats_perdidos_limite=heartbeats_perdidos_limite
        )
        self._falhas_envio[nome_peer] = 0

        if ja_monitorado:
            logger.debug("'%s' reconectou - monitoramento reaproveitado", nome_peer)
            return
        if heartbeats_perdidos_limite is not None:
            logger.info(
                "Heartbeat de '%s': tolerancia propria de %d perdidos (%.0fs)",
                nome_peer,
                heartbeats_perdidos_limite,
                self._health_monitor.timeout_de(nome_peer),
            )

    async def _ao_receber_heartbeat(self, evento: Evento) -> None:
        origem = evento.dados.get("origem")
        if origem not in self._peers:
            return
        self._ja_estabelecidos.add(origem)
        self._health_monitor.receber_heartbeat(origem)
        if origem in self._perdidos_atualmente:
            self._perdidos_atualmente.discard(origem)
            await self._event_bus.publish(
                "comm.module_recovered", {"modulo": origem}, prioridade=Prioridade.ALTA
            )

    async def _marcar_perdido(self, peer: str) -> None:
        """Publica comm.module_lost e tenta reconectar - so uma vez por
        perda (idempotente via _perdidos_atualmente), venha ela de um
        heartbeat que parou de chegar OU de uma falha ao enviar (link
        fechado do outro lado - achado real: sem isso, mandar heartbeat
        pra um peer que ja desconectou gera aviso pra sempre e nunca
        publica module_lost, porque a deteccao antiga so olhava
        heartbeats *recebidos*, nunca falha ao *enviar*).

        Quem nunca conectou ganha uma CARENCIA, nao silencio eterno. No
        boot, o laco de heartbeat comeca antes de o supervisor TCP abrir o
        link: enviar falha com "sem rota" e isso NAO e uma perda, e um
        "ainda nao" - tratar os dois igual gerava "Heartbeat perdido" falso
        em toda partida (visto em 2026-07-19). Mas ignorar PARA SEMPRE quem
        nunca conectou tinha o defeito oposto: um link que morreu de vez
        (ex.: Pi desligado) nunca virava comm.module_lost e ninguem era
        avisado. Entao a carencia dura o mesmo tanto que a tolerancia
        normal do peer (N falhas seguidas); passou disso, e perda de
        verdade.
        """
        if peer not in self._ja_estabelecidos:
            limite = self._limite_perdidos.get(peer, self._limite_global)
            if self._falhas_envio.get(peer, 0) < limite:
                logger.debug("'%s' ainda nao conectou - dentro da carencia", peer)
                return
            logger.warning(
                "'%s' nunca conectou apos %d tentativas - tratando como perda", peer, limite
            )
        if peer in self._perdidos_atualmente:
            return
        self._perdidos_atualmente.add(peer)
        logger.warning("Heartbeat perdido: modulo='%s'", peer)
        await self._event_bus.publish(
            "comm.module_lost", {"modulo": peer}, prioridade=Prioridade.ALTA
        )
        await self._tentar_reconectar(peer)

    async def iniciar(self) -> None:
        """Loop continuo: envia heartbeats e verifica perdas a cada `intervalo_s`."""
        self._executando = True
        while self._executando:
            for peer in self._peers:
                try:
                    await self._servico.enviar_heartbeat(peer)
                except Exception:
                    # Antes do primeiro sucesso isto e rotina (link ainda
                    # nao aberto), depois dele e sintoma - o nivel do log
                    # acompanha, para nao gritar no boot.
                    self._falhas_envio[peer] = self._falhas_envio.get(peer, 0) + 1
                    if peer in self._ja_estabelecidos:
                        logger.warning("Falha ao enviar heartbeat para '%s'", peer)
                    else:
                        logger.debug("Link com '%s' ainda nao aberto", peer)
                    await self._marcar_perdido(peer)
                else:
                    self._falhas_envio[peer] = 0
                    self._ja_estabelecidos.add(peer)

            perdidos_por_recebimento = set(self._health_monitor.modulos_com_heartbeat_perdido())
            for peer in perdidos_por_recebimento - self._perdidos_atualmente:
                await self._marcar_perdido(peer)

            await asyncio.sleep(self._intervalo_s)

    async def _tentar_reconectar(self, peer: str) -> None:
        callback = self._reconectar.get(peer)
        if callback is None:
            return
        try:
            await callback()
        except Exception:
            logger.exception("Falha ao tentar reconectar com '%s'", peer)

    def parar(self) -> None:
        self._executando = False
