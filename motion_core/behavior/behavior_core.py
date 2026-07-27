"""BehaviorCore: o maestro (EDR-0020) - arbitragem por prioridade.

Roda no Raspberry (Motion Core). A cada reavaliação, o comportamento de
MAIOR prioridade que "quer rodar" assume o controle; um mais forte preempta
(cancela a tarefa) o mais fraco, que pode voltar depois. É isto que faz o
Fofão parecer "vivo": ninguém manda o próximo passo, o maestro decide
sozinho a partir do que cada comportamento deseja.
"""
from __future__ import annotations

import asyncio
import logging
import time

from motion_core.behavior.comportamento import Comportamento
from orion.kernel.event_bus import EventBus

logger = logging.getLogger("motion_core.behavior.behavior_core")

#: tick de segurança: mesmo sem gatilho, o maestro reavalia neste intervalo
#: (pega comportamentos que passaram a querer rodar sem avisar).
INTERVALO_TICK_S = 0.2

#: Intervalo MÍNIMO entre duas consultas à IA. Alto de propósito: a
#: inferência local leva ~10-20s e a pergunta ("o que faço enquanto estou
#: ocioso?") não tem urgência nenhuma. Ver _pode_consultar_ia.
INTERVALO_CONSULTA_IA_S = 120.0


class BehaviorCore:
    def __init__(
        self,
        event_bus: EventBus,
        ponte_conselho=None,
        montar_contexto=None,
        intervalo_consulta_ia_s: float = INTERVALO_CONSULTA_IA_S,
    ) -> None:
        self._event_bus = event_bus
        self._comportamentos: list[Comportamento] = []
        self._ativo: Comportamento | None = None
        self._tarefa_ativa: asyncio.Task | None = None
        self._executando = False
        self._acordar = asyncio.Event()

        # --- conselho de IA (opcional) ---
        # A IA só entra na AMBIGUIDADE: quando ninguém com gatilho concreto
        # quer o controle e sobra apenas a base (repouso). Em qualquer
        # situação com dono definido - segurança, voz, alerta - a regra
        # decide sozinha e a IA nem é consultada.
        self._ponte_conselho = ponte_conselho
        self._montar_contexto = montar_contexto
        self._discricionarios: dict[str, Comportamento] = {}
        self._consultando = False
        self._ultima_consulta = 0.0
        self._intervalo_consulta_s = intervalo_consulta_ia_s

    def registrar(self, comportamento: Comportamento, discricionario: bool = False) -> None:
        """Adiciona um comportamento e reordena do mais forte ao mais fraco.

        `discricionario=True` marca um comportamento que o robô PODE escolher
        por iniciativa própria (ex.: ronda) - é entre esses que o conselheiro
        de IA opina. Comportamentos com gatilho concreto nunca são
        discricionários: eles se impõem sozinhos pela prioridade.
        """
        comportamento._maestro = self
        self._comportamentos.append(comportamento)
        self._comportamentos.sort(key=lambda c: c.prioridade, reverse=True)
        if discricionario:
            self._discricionarios[comportamento.nome] = comportamento

    @property
    def nomes_registrados(self) -> list[str]:
        """Nomes dos comportamentos, do mais forte ao mais fraco.

        Existe para o log não repetir a lista à mão - lista escrita à mão
        mente assim que alguém registra um comportamento novo.
        """
        return [c.nome for c in self._comportamentos]

    @property
    def ativo_nome(self) -> str | None:
        """Nome do comportamento no controle agora (None = nenhum)."""
        return self._ativo.nome if self._ativo is not None else None

    def pedir_reavaliacao(self) -> None:
        """Acorda o maestro para reavaliar quem deve estar no controle.
        Um comportamento chama isto quando seu gatilho muda."""
        self._acordar.set()

    def _escolher(self) -> Comportamento | None:
        # lista já ordenada por prioridade decrescente: o primeiro que quer
        # rodar é o vencedor.
        for comportamento in self._comportamentos:
            if comportamento.quer_rodar():
                return comportamento
        return None

    def _pode_consultar_ia(self, escolhido: Comportamento | None) -> bool:
        """Só na ambiguidade, espaçado no tempo, e uma consulta por vez.

        O INTERVALO é essencial, não um detalhe de performance. Sem ele o
        maestro reperguntava a cada timeout (~6s) enquanto a inferência do
        modelo levava mais de 8s: os pedidos empilhavam mais rápido do que
        eram respondidos, o Notebook afogava e o link TCP caía e reconectava
        em laço (medido ao vivo em 2026-07-19, com vazamento de dezenas de
        conexões). Ociosidade não é urgência - perguntar de minuto em minuto
        é mais que suficiente.
        """
        if self._ponte_conselho is None or self._consultando:
            return False
        if time.monotonic() - self._ultima_consulta < self._intervalo_consulta_s:
            return False
        if not self._discricionarios:
            return False  # sem opção discricionária não há o que aconselhar
        # Algum comportamento de gatilho no controle? Então a regra decidiu.
        if escolhido is not None and escolhido.prioridade > self._prioridade_base():
            return False
        # Um discricionário já quer rodar? Já foi decidido, não repergunta.
        return not any(c.quer_rodar() for c in self._discricionarios.values())

    def _prioridade_base(self) -> int:
        """Prioridade do comportamento mais fraco (a base, tipo repouso)."""
        return min((c.prioridade for c in self._comportamentos), default=0)

    async def _consultar_ia(self) -> None:
        """Pergunta à IA o que fazer na ociosidade e aplica se fizer sentido.

        Blindagem: qualquer resposta que não seja exatamente o nome de um
        discricionário registrado é ignorada. A IA não tem como acionar
        segurança, voz ou alerta por aqui - esses nem entram na lista
        oferecida a ela.
        """
        try:
            opcoes = sorted(self._discricionarios) + ["repouso"]
            contexto = self._montar_contexto() if self._montar_contexto else ""
            resposta = await self._ponte_conselho.pedir(contexto, opcoes)
            if not resposta:
                return

            nome = resposta.get("comportamento")
            alvo = self._discricionarios.get(nome)
            if alvo is None:
                # Inclui o caso "repouso": conselho de não fazer nada é
                # legítimo e simplesmente não muda nada.
                logger.debug("conselho '%s' não aciona discricionário", nome)
                return

            logger.info(
                "maestro: IA sugeriu '%s' (%s)", nome, resposta.get("motivo", "")
            )
            alvo.pedir()
        except Exception:
            logger.exception("consulta à IA falhou - seguindo pela regra")
        finally:
            self._consultando = False

    async def _trocar_para(self, novo: Comportamento | None) -> None:
        if self._tarefa_ativa is not None and not self._tarefa_ativa.done():
            self._tarefa_ativa.cancel()
            try:
                await self._tarefa_ativa
            except asyncio.CancelledError:
                pass
        self._ativo = novo
        self._tarefa_ativa = None
        if novo is not None:
            logger.info("maestro: '%s' assume (prio %d)", novo.nome, novo.prioridade)
            self._tarefa_ativa = asyncio.create_task(self._rodar(novo))

    async def _rodar(self, comportamento: Comportamento) -> None:
        try:
            await comportamento.executar()
        except asyncio.CancelledError:
            logger.info("maestro: '%s' preemptado", comportamento.nome)
            raise
        except Exception:
            logger.exception("maestro: erro em '%s' (isolado)", comportamento.nome)
        finally:
            # terminou sozinho -> reavaliar quem entra agora
            self.pedir_reavaliacao()

    async def executar(self) -> None:
        """Laço principal do maestro. Roda até `parar()`."""
        self._executando = True
        while self._executando:
            # tarefa que acabou sozinha deixa de ser a ativa
            if self._tarefa_ativa is not None and self._tarefa_ativa.done():
                self._ativo = None
                self._tarefa_ativa = None

            escolhido = self._escolher()

            # Ambiguidade = só a base quer rodar. É a única hora em que a
            # opinião da IA pode mudar alguma coisa; nas demais, quem tem
            # gatilho concreto já venceu e consultar seria só atraso.
            if self._pode_consultar_ia(escolhido):
                self._consultando = True
                self._ultima_consulta = time.monotonic()
                asyncio.create_task(self._consultar_ia())

            if escolhido is not self._ativo:
                # escolhido é sempre o de maior prioridade que quer rodar;
                # se difere do ativo, ele é o dono legítimo do controle.
                await self._trocar_para(escolhido)

            self._acordar.clear()
            try:
                await asyncio.wait_for(self._acordar.wait(), timeout=INTERVALO_TICK_S)
            except asyncio.TimeoutError:
                pass

        await self._trocar_para(None)

    def parar(self) -> None:
        self._executando = False
        self._acordar.set()
