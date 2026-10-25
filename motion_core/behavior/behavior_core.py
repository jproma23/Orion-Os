"""BehaviorCore: o maestro (EDR-0020) - arbitragem por prioridade.

Roda no Raspberry (Motion Core). A cada reavaliação, o comportamento de
MAIOR prioridade que "quer rodar" assume o controle; um mais forte preempta
(cancela a tarefa) o mais fraco, que pode voltar depois. É isto que faz o
Jarvis parecer "vivo": ninguém manda o próximo passo, o maestro decide
sozinho a partir do que cada comportamento deseja.
"""
from __future__ import annotations

import asyncio
import logging
import time

from motion_core.behavior.comportamento import (
    AUTORIDADE_AUTONOMA,
    AUTORIDADE_MANUAL,
    AUTORIDADE_SEMPRE,
    Comportamento,
    resolver_contexto,
)
from motion_core.behavior.memoria_decisoes import MemoriaDeDecisoes, assinatura
from orion.kernel.event_bus import EventBus

logger = logging.getLogger("motion_core.behavior.behavior_core")

#: O robô no volante: a IA estratégica e a ronda comandam; o joystick não.
MODO_AUTONOMO = "autonomo"
#: O operador no volante: o joystick comanda; a IA opina e não executa.
MODO_MANUAL = "manual"

MODOS_DE_CONTROLE = (MODO_AUTONOMO, MODO_MANUAL)

#: Quanto tempo o operador pode ficar sem tocar em nada antes de o volante
#: voltar sozinho para o robô (2026-07-30).
#:
#: Pedido do dono: *"quando passar mais de 3 minutos ocioso passar para auto"*,
#: coerente com *"prefiro ele autonomo sempre"*. Sem isto, um único clique em
#: "manual" deixava o Fofão parado para sempre, esperando alguém lembrar de
#: devolver o controle - e foi exatamente o que aconteceu na bancada hoje.
#:
#: O manual é a INTERVENÇÃO, não o estado de repouso do robô: o estado de
#: repouso é ele cuidando da casa. Então o esquecimento não pode custar a
#: autonomia. Três minutos é longo o bastante para o operador pensar entre duas
#: manobras sem o robô "fugir" da mão dele (o joystick solta o controle em
#: 1,5 s, mas ISTO conta ociosidade real, não pausa entre dois empurrões).
SEGUNDOS_OCIOSO_ATE_AUTO = 180.0

#: Que autoridade tem a mão em cada modo. A segurança reativa, a voz e o
#: gesto são `AUTORIDADE_SEMPRE` e portanto valem nos dois - trocar de modo
#: nunca desliga nada disso.
_AUTORIDADE_DO_MODO = {
    MODO_AUTONOMO: (AUTORIDADE_SEMPRE, AUTORIDADE_AUTONOMA),
    MODO_MANUAL: (AUTORIDADE_SEMPRE, AUTORIDADE_MANUAL),
}

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
        memoria_decisoes: MemoriaDeDecisoes | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._comportamentos: list[Comportamento] = []
        self._ativo: Comportamento | None = None
        self._tarefa_ativa: asyncio.Task | None = None
        self._executando = False
        self._acordar = asyncio.Event()

        #: QUEM ESTÁ NO VOLANTE - escolha explícita do dono, pelo botão do
        #: dashboard (2026-07-30). Começa em AUTÔNOMO porque é o estado normal
        #: do robô: ele existe para agir sozinho, e o manual é a intervenção.
        #:
        #: Mora aqui, e só aqui, porque o maestro é quem arbitra - guardar uma
        #: segunda cópia na interface criaria duas fontes de verdade sobre o
        #: mesmo fato, que é a família de bug que mais se repete neste projeto.
        self._modo_controle = MODO_AUTONOMO
        #: Último instante em que alguém com autoridade manual quis o controle.
        #: Base da volta automática para autônomo - ver `_checar_ocioso`.
        self._ultima_atividade_manual = 0.0

        # --- conselho de IA (opcional) ---
        # A IA só entra na AMBIGUIDADE: quando ninguém com gatilho concreto
        # quer o controle e sobra apenas a base (repouso). Em qualquer
        # situação com dono definido - segurança, voz, alerta - a regra
        # decide sozinha e a IA nem é consultada.
        self._ponte_conselho = ponte_conselho
        self._montar_contexto = montar_contexto
        self._discricionarios: dict[str, Comportamento] = {}
        self._consultando = False
        #: Referencia FORTE da consulta em voo (varredura de 2026-08-04).
        #:
        #: `asyncio.create_task` sozinho nao segura a tarefa: o loop so guarda
        #: referencia fraca, e a documentacao do asyncio manda guardar o
        #: resultado "para a tarefa nao desaparecer no meio da execucao". Se
        #: ela sumisse aqui, o `finally` que devolve `_consultando` para False
        #: nunca rodaria - e `_pode_consultar_ia` passaria a recusar TODA
        #: consulta seguinte, para sempre, sem uma linha de log.
        #:
        #: Falha silenciosa e permanente, no mesmo caminho onde 5444 decisoes
        #: da IA ja evaporaram uma vez (30/07).
        self._tarefa_consulta: asyncio.Task | None = None
        self._ultima_consulta = 0.0
        self._intervalo_consulta_s = intervalo_consulta_ia_s
        #: O robo pergunta UMA VEZ por situacao e guarda a resposta. Em
        #: 07/08 ele fez 505 chamadas ao modelo para tomar 4 decisoes
        #: identicas - com os sensores mudos a situacao nao mudava, e ele
        #: reperguntava do mesmo jeito. Ver memoria_decisoes.py.
        #:
        #: NAO se cria uma sozinho aqui: `MemoriaDeDecisoes()` aponta para
        #: /mnt/ssd/orion/ e, como default, fez um teste UNITARIO gravar no
        #: arquivo de PRODUCAO e contaminar o teste seguinte (07/08).
        #: Componente que grava em disco recebe o disco. Sem ela, o maestro
        #: se comporta exatamente como antes - pergunta sempre.
        self._decisoes_aprendidas = memoria_decisoes

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

    @property
    def modo_controle(self) -> str:
        """Quem está no volante agora: `autonomo` ou `manual`."""
        return self._modo_controle

    def tem_o_volante(self, comportamento: Comportamento) -> bool:
        """True se a autoridade deste comportamento vale no modo atual."""
        return comportamento.autoridade in _AUTORIDADE_DO_MODO[self._modo_controle]

    async def definir_modo_controle(self, modo: str) -> None:
        """Troca quem está no volante e reavalia na hora.

        A troca PREEMPTA quem estava no controle sem a autoridade nova - é o
        ponto do botão: sair de autônomo tira as rodas da IA imediatamente, e
        não no fim do que ela estava fazendo.
        """
        if modo not in MODOS_DE_CONTROLE:
            raise ValueError(
                f"modo de controle invalido: {modo!r} - use um de {list(MODOS_DE_CONTROLE)}"
            )
        if modo == self._modo_controle:
            return
        anterior = self._modo_controle
        self._modo_controle = modo
        # Entrar em manual zera o cronômetro de ociosidade: os 3 minutos contam
        # da entrega do volante, não de um comando que ainda não veio.
        self._ultima_atividade_manual = time.monotonic()
        logger.info("maestro: volante entregue ao modo '%s' (era '%s')", modo, anterior)
        await self._event_bus.publish(
            "behavior.modo_controle", {"modo": modo, "anterior": anterior}
        )
        self.pedir_reavaliacao()

    async def _checar_ocioso(self) -> None:
        """Devolve o volante ao robô depois de `SEGUNDOS_OCIOSO_ATE_AUTO` sem uso.

        Não assina evento novo nem consulta um comportamento pelo nome: usa o
        `quer_rodar()` de quem TEM autoridade manual, que já é exatamente "houve
        comando do operador há pouco" (o `ControleManual` o mantém True por
        1,5 s a cada toque no joystick). Ociosidade aqui é o operador parado de
        verdade, não a pausa entre dois empurrões.
        """
        if self._modo_controle != MODO_MANUAL:
            return
        agora = time.monotonic()
        if any(
            c.autoridade == AUTORIDADE_MANUAL and c.quer_rodar() for c in self._comportamentos
        ):
            self._ultima_atividade_manual = agora
            return
        if agora - self._ultima_atividade_manual < SEGUNDOS_OCIOSO_ATE_AUTO:
            return
        logger.info(
            "maestro: %.0f s sem comando manual - devolvendo o volante ao modo automatico",
            SEGUNDOS_OCIOSO_ATE_AUTO,
        )
        await self.definir_modo_controle(MODO_AUTONOMO)

    def _escolher(self) -> Comportamento | None:
        # lista já ordenada por prioridade decrescente: o primeiro que quer
        # rodar é o vencedor - desde que a autoridade dele valha no modo de
        # controle atual. Sem este filtro, prioridade alta virava autoridade:
        # o joystick (95) e a vigilância (100) atropelavam a IA (40) mesmo
        # quando o dono tinha entregue o volante a ela.
        for comportamento in self._comportamentos:
            if comportamento.quer_rodar() and self.tem_o_volante(comportamento):
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
            contexto = await resolver_contexto(self._montar_contexto)

            # JA PERGUNTEI ISTO? A assinatura sai do proprio texto que iria
            # ao modelo: mesma entrada, mesma resposta. Se a situacao nao
            # mudou, a chamada nao acontece - e foi exatamente isto que
            # esgotou a cota do dia em 07/08.
            chave = assinatura(contexto, opcoes) if self._decisoes_aprendidas else ""
            resposta = (
                self._decisoes_aprendidas.lembrar(chave)
                if self._decisoes_aprendidas
                else None
            )
            if resposta is not None:
                logger.debug(
                    "maestro: situacao ja conhecida - reaproveitando '%s' sem perguntar",
                    resposta.get("comportamento"),
                )
            else:
                resposta = await self._ponte_conselho.pedir(contexto, opcoes)
                if not resposta:
                    return
                nome_aprendido = resposta.get("comportamento")
                if nome_aprendido and self._decisoes_aprendidas:
                    self._decisoes_aprendidas.aprender(
                        chave, nome_aprendido, resposta.get("motivo", "")
                    )
                    logger.info(
                        "maestro: aprendi esta situacao (%s)",
                        self._decisoes_aprendidas.resumo(),
                    )

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

            # Antes de escolher: o volante ainda deve estar com quem está?
            await self._checar_ocioso()

            escolhido = self._escolher()

            # Ambiguidade = só a base quer rodar. É a única hora em que a
            # opinião da IA pode mudar alguma coisa; nas demais, quem tem
            # gatilho concreto já venceu e consultar seria só atraso.
            if self._pode_consultar_ia(escolhido):
                self._consultando = True
                self._ultima_consulta = time.monotonic()
                self._tarefa_consulta = asyncio.create_task(self._consultar_ia())

            if escolhido is not self._ativo:
                # escolhido é sempre o de maior prioridade que quer rodar;
                # se difere do ativo, ele é o dono legítimo do controle.
                await self._trocar_para(escolhido)

            self._acordar.clear()
            try:
                await asyncio.wait_for(self._acordar.wait(), timeout=INTERVALO_TICK_S)
            except TimeoutError:
                pass

        await self._trocar_para(None)

    def parar(self) -> None:
        self._executando = False
        self._acordar.set()
