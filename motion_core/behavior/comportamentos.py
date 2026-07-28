"""Comportamentos concretos do Fofão plugados no maestro (EDR-0020).

Começo enxuto, self-contained no Pi (usa sinais que já chegam ao Event Bus
do Motion Core): Repouso (base) e VigilanciaObstaculo (segurança tática).
Outros (Atender por voz, Vigília Sentinela, Patrulha) entram depois.
"""
from __future__ import annotations

import asyncio
import logging
import time

from motion_core.behavior import contrato
from motion_core.behavior.comportamento import Comportamento
from orion.kernel.event_bus import EventBus, Evento, Prioridade

logger = logging.getLogger("motion_core.behavior.comportamentos")


class Repouso(Comportamento):
    """Base da escada (prio 10): quer rodar sempre, mas perde para qualquer
    outro. Enquanto no controle, o robô está de prontidão, sem fazer nada -
    é o estado "nada acontecendo"."""

    nome = "repouso"
    prioridade = 10

    def quer_rodar(self) -> bool:
        return True  # sempre disponível; só assume quando ninguém mais quer

    async def executar(self) -> None:
        await self._event_bus.publish("behavior.status", {"estado": "repouso"})
        # Nivela a cabeça ao assumir. Sem isto, depois de um reset do Mega
        # (que acontece a cada reconexão serial) os servos ficam no padrão do
        # firmware - apontando para baixo - e nada os traz de volta, porque
        # o repouso não comandava pan/tilt. Prontidão é com a cabeça no nível.
        await self._event_bus.publish("motion.pan_tilt", {"pan": 0, "tilt": 0})
        logger.info("maestro: em repouso (prontidão), cabeça nivelada")
        while True:
            await asyncio.sleep(1.0)


class Ronda(Comportamento):
    """Iniciativa própria (prio 20): dar uma olhada em volta quando não há
    nada acontecendo.

    É o primeiro comportamento DISCRICIONÁRIO do Fofão - os outros são
    disparados por condição concreta (voz, alerta, obstáculo), este é uma
    escolha. Existe justamente para o conselheiro de IA ter o que escolher:
    parado (repouso) ou dar uma olhada (ronda). Com uma opção só não há
    conselho a dar.

    Só olha, NÃO anda: varre o radar e mexe o pan/tilt. Movimento de rodas
    por iniciativa da IA seria arriscado demais para o primeiro passo -
    uma olhada em volta é reversível e não machuca ninguém.
    """

    nome = "ronda"
    prioridade = 20  # acima de repouso (10), abaixo de tudo que é gatilho

    def __init__(self, event_bus: EventBus, intervalo_s: float = 60.0) -> None:
        super().__init__(event_bus)
        self._intervalo_s = intervalo_s
        self._pedida = False

    def pedir(self) -> None:
        """Chamado quando o conselheiro sugere `ronda`."""
        if not self._pedida:
            self._pedida = True
            self._reavaliar()

    def quer_rodar(self) -> bool:
        return self._pedida

    async def executar(self) -> None:
        await self._event_bus.publish("behavior.status", {"estado": "ronda"})
        logger.info("maestro: ronda - dando uma olhada em volta")
        try:
            await self._event_bus.publish("motion.pan_tilt", {"pan": -60, "tilt": 0})
            await asyncio.sleep(1.5)
            await self._event_bus.publish("motion.pan_tilt", {"pan": 60, "tilt": 0})
            await asyncio.sleep(1.5)
            await self._event_bus.publish("motion.pan_tilt", {"pan": 0, "tilt": 0})
            await self._event_bus.publish("navigation.comando", {"acao": "SCAN_FRONT"})
            await asyncio.sleep(2.0)
        finally:
            # Terminou (ou foi preemptada): a ronda não fica pendurada
            # querendo o controle de novo.
            self._pedida = False
            self._reavaliar()


class AcompanharPessoa(Comportamento):
    """Segurar o controle enquanto a visão está rastreando alguém (prio 45).

    NÃO comanda o pan/tilt: quem move o servo é o Vision Core, no Notebook,
    que calcula a correção quadro a quadro. Este comportamento existe só para
    IMPEDIR que outro comportamento mexa na cabeça no meio do rastreamento.

    Sem ele o sintoma era exatamente "segue e depois se perde" (relatado em
    2026-07-28): `Repouso` publica `pan=0, tilt=0` toda vez que ASSUME, e o
    maestro volta ao repouso a cada preempção da vigilância de obstáculo -
    que pisca sozinha quando há qualquer coisa à frente. A cada volta a
    cabeça era recentrada por baixo do rastreamento, a pessoa saía do quadro
    e o alvo se perdia. Dois comandantes disputavam o mesmo servo sem
    ninguém arbitrando; agora a disputa passa pelo maestro, como manda o
    EDR-0020.

    Prioridade 45: acima do que é DISCRICIONÁRIO (ronda 20, mentor 35, IA
    estratégica 40 - esta última chega a varrer -60/+60 em `observar_ambiente`,
    o que arrancaria a câmera de cima da pessoa), e abaixo do que é mais
    importante que olhar alguém: vigília de rosto desconhecido (60), atender
    o dono (80), controle manual (95) e segurança (100).

    Gatilhos vêm do Notebook: vision.person_detected e vision.person_lost.
    Eles SÓ chegam aqui porque `conversar_fofao.py` os encaminha - até
    2026-07-28 não encaminhava, e todo assinante de visão no Pi (inclusive
    o EstadoDoMundo) esperava por um evento que nunca vinha.
    """

    nome = "acompanhar_pessoa"
    prioridade = 45

    #: Solta a cabeça se parar de chegar notícia de pessoa por este tempo.
    #: NÃO depende de `vision.person_lost` chegar: aquele evento só é
    #: publicado se o rastreador chegou a CONFIRMAR um alvo (3 quadros), então
    #: uma detecção que nunca confirma não gera "perdi" nenhum. Sem este
    #: timeout o comportamento seguraria a cabeça para sempre e o Repouso
    #: nunca mais nivelaria - troca de um bug por outro pior.
    TIMEOUT_SEM_NOTICIA_S = 3.0

    def __init__(self, event_bus: EventBus, timeout_s: float | None = None) -> None:
        super().__init__(event_bus)
        self._timeout_s = timeout_s if timeout_s is not None else self.TIMEOUT_SEM_NOTICIA_S
        self._vendo = False
        self._ultimo_visto = 0.0
        event_bus.subscribe("vision.person_detected", self._ao_ver)
        event_bus.subscribe("vision.person_lost", self._ao_perder)

    async def _ao_ver(self, evento: Evento) -> None:
        self._ultimo_visto = time.monotonic()
        if not self._vendo:
            self._vendo = True
            self._reavaliar()

    async def _ao_perder(self, evento: Evento) -> None:
        # Caminho rápido: a visão avisou que perdeu, não espera o timeout.
        if self._vendo:
            self._vendo = False
            self._reavaliar()

    def quer_rodar(self) -> bool:
        return self._vendo

    async def executar(self) -> None:
        await self._event_bus.publish("behavior.status", {"estado": "acompanhando"})
        logger.info("maestro: acompanhando pessoa - cabeça entregue à visão")
        try:
            while True:
                await asyncio.sleep(0.5)
                if time.monotonic() - self._ultimo_visto > self._timeout_s:
                    logger.info(
                        "maestro: %.1fs sem notícia de pessoa - soltando a cabeça",
                        self._timeout_s,
                    )
                    self._vendo = False
                    self._reavaliar()
                    return
        except asyncio.CancelledError:
            # Ao soltar o controle o Repouso assume e nivela a cabeça - que é
            # o comportamento certo quando não há mais ninguém para seguir.
            logger.info("maestro: parei de acompanhar - cabeça volta ao maestro")
            raise


class Atender(Comportamento):
    """Atender o dono (prio 80): quando alguém chama "Fofão", o robô para o
    que estava fazendo e fica à disposição até a resposta terminar. A
    conversa em si acontece no Notebook (Voice Core); aqui o maestro apenas
    garante que o robô NÃO saia andando enquanto atende - manda HOLD ao
    Motion Core e segura o controle.

    Gatilhos vêm do Notebook, encaminhados pelo Event Bus: voice.wake_detected
    (começou) e voice.response_finished (terminou)."""

    nome = "atender"
    prioridade = 80

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self._atendendo = False
        event_bus.subscribe("voice.wake_detected", self._ao_acordar)
        event_bus.subscribe("voice.response_finished", self._ao_terminar)

    async def _ao_acordar(self, evento: Evento) -> None:
        if not self._atendendo:
            self._atendendo = True
            self._reavaliar()

    async def _ao_terminar(self, evento: Evento) -> None:
        if self._atendendo:
            self._atendendo = False
            self._reavaliar()

    def quer_rodar(self) -> bool:
        return self._atendendo

    async def executar(self) -> None:
        await self._event_bus.publish("behavior.status", {"estado": "atendendo"})
        # para o robô enquanto atende (se estava patrulhando/movendo).
        await self._event_bus.publish("navigation.comando", {"acao": "HOLD"})
        logger.info("maestro: dono chamou - atendendo (robô em espera)")
        try:
            while True:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("maestro: atendimento encerrado")
            raise


class Vigilia(Comportamento):
    """Vigília / Modo Sentinela (prio 60): entre Atender (80) e Patrulha
    (40). Dispara ao receber `sentinela.alerta` (barulho estranho ou rosto
    desconhecido, detectados no Notebook e encaminhados ao Pi). Ao assumir:
    pede foto na direção (`sentinela.capturar_foto`), notifica e segura o
    controle por `duracao_alerta_s`, depois libera. Um alerta mais forte
    (Atender/Segurança) preempta normalmente.

    Os DETECTORES que produzem `sentinela.alerta` (anomalia de som, rosto
    desconhecido) ainda serão construídos no Notebook - este é o slot que
    reage a eles."""

    nome = "vigilia"
    prioridade = 60

    def __init__(self, event_bus: EventBus, duracao_alerta_s: float) -> None:
        super().__init__(event_bus)
        self._duracao_alerta_s = duracao_alerta_s
        self._alerta = False
        self._tipo = ""
        event_bus.subscribe("sentinela.alerta", self._ao_receber_alerta)

    async def _ao_receber_alerta(self, evento: Evento) -> None:
        self._tipo = evento.dados.get("tipo", "desconhecido")
        if not self._alerta:
            self._alerta = True
            self._reavaliar()

    def quer_rodar(self) -> bool:
        return self._alerta

    async def executar(self) -> None:
        logger.warning("maestro: VIGÍLIA - alerta '%s', investigando", self._tipo)
        await self._event_bus.publish(
            "behavior.status", {"estado": "vigilia", "tipo": self._tipo}, prioridade=Prioridade.ALTA
        )
        # pede uma foto da origem e notifica (o Notebook/interface tratam).
        await self._event_bus.publish("sentinela.capturar_foto", {"motivo": self._tipo})
        await self._event_bus.publish(
            "diagnostic.alerta", {"origem": "vigilia", "tipo": self._tipo}, prioridade=Prioridade.ALTA
        )
        # se preemptado (obstáculo/atender) durante a espera, CancelledError
        # sobe e _alerta continua True -> a Vigília RETOMA depois. Só libera
        # o controle ao concluir a investigação por completo.
        await asyncio.sleep(self._duracao_alerta_s)
        self._alerta = False
        self._reavaliar()


class IaEstrategica(Comportamento):
    """A IA do Fofao no comando (prio 40) - EDR-0022 + EDR-0026.

    **Uma personalidade so.** Ate 2026-07-28 havia duas: esta e o `Mentor`
    (prio 35). A separacao fazia sentido quando o Mentor era um modelo LOCAL
    (gemma3:1b no Notebook, sem internet) e esta era a remota - segunda
    opiniao de verdade, com modo de falha independente. Isso acabou em
    2026-07-24, quando o Ollama local saiu por travar o Notebook: desde entao
    **as duas falavam com o mesmo provedor**, e o Mentor virou a mesma IA
    perguntada de novo, mais devagar. Pior: `Mentor.executar()` so publicava
    status e dormia - o conselho nunca virava acao nenhuma.

    Enquanto nenhuma das duas movia o robo, isso era redundancia inutil. Com
    autoridade motora (EDR-0026) viraria perigo: duas instancias da mesma IA
    disputando as rodas em ritmos diferentes (30s e 90s), uma preemptando a
    outra no meio do movimento. Nas palavras do usuario: *"nao podemos ter
    duas personalidades no controle, uma so assume tudo"*.

    Ficou esta, herdando o que o Mentor tinha de melhor:

    - **Protocolo do conselheiro** (`behavior.aconselhar`): saida JSON com
      enum, que nunca inventa nome de acao. O caminho antigo
      (`mission.decidir`) casava a resposta por SUBSTRING - e "parar" casa
      dentro de "preparar".
    - **Motivo junto da decisao**, que vai para o log e para a interface.
    - **Guarda de seguranca**: enquanto um comportamento de seguranca quiser
      o controle, nem se pergunta a IA.

    O que ela pode fazer esta em `contrato.py` - nao aqui, e nunca no prompt.
    """

    nome = "ia_estrategica"
    prioridade = 40

    def __init__(
        self,
        event_bus: EventBus,
        comm,
        intervalo_s: float = 30.0,
        acoes_validas: tuple[str, ...] = contrato.NOMES,
    ) -> None:
        super().__init__(event_bus)
        self._comm = comm
        self._intervalo_s = intervalo_s
        #: O vocabulario vem do CONTRATO, nao do orion.yaml: e o limite da
        #: autoridade da IA sobre o robo, e limite de autoridade nao e
        #: parametro de ajuste. Continua injetavel para os testes poderem
        #: estreitar o catalogo, nunca para alarga-lo.
        self._acoes_validas = acoes_validas
        self._acao_atual: str | None = None
        self._motivo_atual: str = ""
        self._executando = False

    def iniciar(self) -> asyncio.Task:
        """Inicia o laco de consulta em segundo plano - chamar uma vez no
        boot do Motion Core, fora do tick de 200ms do maestro."""
        self._executando = True
        return asyncio.create_task(self._loop_consulta())

    def parar(self) -> None:
        self._executando = False

    async def _loop_consulta(self) -> None:
        while self._executando:
            await self._consultar()
            await asyncio.sleep(self._intervalo_s)

    async def _consultar(self) -> None:
        nome_ativo = self._maestro.ativo_nome if self._maestro else None
        # Seguranca nao se negocia: com a vigilancia de obstaculo no comando,
        # nem se pergunta a IA (Cap 18, regra 3 do contrato).
        seguranca_ativa = nome_ativo == "vigilancia_obstaculo"
        estado = {"comportamento_ativo": nome_ativo}
        try:
            resposta = await self._comm.request(
                "mission_core",
                {
                    "comando": "behavior.aconselhar",
                    "contexto": str(estado),
                    "opcoes": list(self._acoes_validas),
                    "seguranca_ativa": seguranca_ativa,
                },
                timeout_s=25.0,
            )
            ok = resposta.payload.get("ok")
            escolha = resposta.payload.get("comportamento") if ok else None
            motivo = resposta.payload.get("motivo", "") if ok else ""
        except Exception:
            logger.debug("IA indisponivel (sem Notebook/internet?)", exc_info=True)
            escolha, motivo = None, ""

        acao = contrato.resolver(escolha)
        if escolha and acao is None:
            # Regra 6 do contrato: fora do catalogo nunca vira chute.
            logger.info("maestro: IA respondeu %r, fora do contrato - abstendo", escolha)

        nome_acao = acao.nome if acao is not None else None
        mudou = nome_acao != self._acao_atual
        self._acao_atual = nome_acao
        self._motivo_atual = motivo
        if mudou:
            self._reavaliar()

    def quer_rodar(self) -> bool:
        return self._acao_atual is not None

    async def executar(self) -> None:
        acao = contrato.resolver(self._acao_atual)
        await self._event_bus.publish(
            "behavior.status",
            {"estado": self.nome, "acao": self._acao_atual, "motivo": self._motivo_atual},
        )
        logger.info("maestro: IA decidiu '%s' (%s)", self._acao_atual, self._motivo_atual)
        if acao is not None:
            # UMA vez por ativacao, nunca em laco: repetir empurraria o robo
            # indefinidamente enquanto a IA mantivesse a mesma decisao.
            #
            # E sempre por EVENTO, nunca comm.send direto ao Mega: movimento
            # autonomo passa pela navegacao, que checa obstaculo antes de
            # avancar (regra 2 do contrato). O `_avancar` que existia aqui
            # ate 2026-07-28 falava direto com o Arduino e pulava essa camada.
            await self._event_bus.publish(acao.topico, dict(acao.dados))
        try:
            while True:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("maestro: IA preemptada")
            raise


class AtenderGesto(Comportamento):
    """Reage a gesto de mao (prio 70) - ligado em 2026-07-28.

    Os gestos ja eram reconhecidos desde 20/07 (`orion/vision/gestos.py`, o
    canal do Kamal, que tem 3 anos e nao digita), mas morriam no barramento
    do Notebook: ninguem no maestro escutava `gesto.detectado`, e o repasse
    ao Pi nem existia. O robo via o gesto e nao fazia nada.

    Vocabulario (definido pelo dono):

    - **vem** (mao aberta balancando) -> o robo VAI ate quem chamou, entrando
      em FOLLOW. E o unico gesto com consequencia motora.
    - **sim** (joinha) -> confirmacao. Hoje nao ha nada pendente para
      confirmar, entao so registra e avisa: quando existir uma pergunta em
      aberto ("posso ir ate ai?"), e aqui que ela sera respondida.

    Prioridade 70: acima da Vigilia (60), porque uma pessoa chamando vale
    mais que ronda de vigilancia; abaixo do Atender por voz (80), porque
    quem fala esta pedindo algo explicito, e abaixo de controle manual (95)
    e seguranca (100), que ganham sempre.
    """

    nome = "atender_gesto"
    prioridade = 70

    def __init__(self, event_bus: EventBus, duracao_s: float = 8.0) -> None:
        super().__init__(event_bus)
        self._duracao_s = duracao_s
        self._gesto: str | None = None
        event_bus.subscribe("gesto.detectado", self._ao_ver_gesto)

    async def _ao_ver_gesto(self, evento: Evento) -> None:
        tipo = evento.dados.get("tipo")
        if tipo in ("vem", "sim") and self._gesto is None:
            self._gesto = tipo
            self._reavaliar()

    def quer_rodar(self) -> bool:
        return self._gesto is not None

    async def executar(self) -> None:
        gesto = self._gesto
        logger.info("maestro: GESTO '%s' - atendendo", gesto)
        await self._event_bus.publish(
            "behavior.status", {"estado": self.nome, "gesto": gesto}, prioridade=Prioridade.ALTA
        )
        try:
            if gesto == "vem":
                # Chamou: o robo vai. FOLLOW e o modo que ja sabe se
                # aproximar de quem ele reconhece - nao se inventa um
                # caminho novo aqui.
                await self._event_bus.publish("navigation.comando", {"acao": "FOLLOW"})
            await asyncio.sleep(self._duracao_s)
        finally:
            # Solta o controle mesmo se preemptado no meio, senao o gesto
            # ficaria pendurado pedindo a vez para sempre.
            self._gesto = None
            self._reavaliar()


class VigilanciaObstaculo(Comportamento):
    """Segurança tática (prio 100): assume o controle quando o Hardware Core
    reporta OBSTACLE_DETECTED (via motion.status, que já chega ao Event Bus
    do Pi). Acima da camada reativa do Arduino (Cap 18), que é independente.
    Enquanto há obstáculo, segura o controle e publica o alerta; some do
    controle quando o estado sai de OBSTACLE_DETECTED."""

    nome = "vigilancia_obstaculo"
    prioridade = 100

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self._obstaculo = False
        event_bus.subscribe("motion.status", self._ao_mudar_estado)

    async def _ao_mudar_estado(self, evento: Evento) -> None:
        obstaculo_agora = evento.dados.get("estado") == "OBSTACLE_DETECTED"
        if obstaculo_agora != self._obstaculo:
            self._obstaculo = obstaculo_agora
            self._reavaliar()  # acorda o maestro para trocar de controle

    def quer_rodar(self) -> bool:
        return self._obstaculo

    async def executar(self) -> None:
        await self._event_bus.publish(
            "behavior.status", {"estado": "obstaculo"}, prioridade=Prioridade.ALTA
        )
        logger.warning("maestro: obstáculo à frente - segurança assumiu o controle")
        try:
            while True:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("maestro: obstáculo liberado - saindo da vigilância")
            raise


#: Tempo máximo sem um comando manual novo antes do maestro considerar que o
#: operador soltou o controle. Bem acima do intervalo de repetição do
#: joystick (300ms, dashboard.js) e bem abaixo do timeout de segurança do
#: próprio Mega (5s, TIMEOUT_COMANDO_MS em safety_manager.h) - assim o
#: maestro libera o robô para a autonomia antes que a rede de segurança
#: reativa precise agir.
TIMEOUT_INATIVIDADE_MANUAL_S = 1.5


class ControleManual(Comportamento):
    """Controle manual via dashboard (prio 95): entre Segurança (100) e
    Atender (80) - o operador dirigindo tem prioridade sobre qualquer
    decisão autônoma, mas nunca sobre a segurança reativa.

    Até 2026-07-27 o endpoint /comando da webui falava com o Hardware Core
    DIRETO (comm.send), pulando o NavigationCore inteiro: o modo nunca virava
    MANUAL e o maestro não sabia que alguém tinha assumido o controle pelo
    dashboard, então um comportamento autônomo podia mandar comando por cima
    do joystick a qualquer momento sem que ninguém soubesse (achado real -
    era a razão do joystick não mover o robô no primeiro teste). Agora o
    endpoint publica em navigation.comando (acao=MANUAL) como qualquer outro
    módulo (regra 1: tudo pelo Event Bus); o NavigationCore executa, e este
    comportamento aqui SÓ acompanha a presença do operador para arbitrar com
    o maestro - não fala com o hardware."""

    nome = "controle_manual"
    prioridade = 95

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        #: 0.0 = nenhum comando manual ainda nesta execução (ver quer_rodar).
        self._ultimo_comando_monotonic = 0.0
        event_bus.subscribe("navigation.comando", self._ao_receber_comando)

    async def _ao_receber_comando(self, evento: Evento) -> None:
        if evento.dados.get("acao") != "MANUAL":
            return
        ja_ativo = self.quer_rodar()
        self._ultimo_comando_monotonic = time.monotonic()
        if not ja_ativo:
            self._reavaliar()  # acorda o maestro para assumir a prioridade

    def quer_rodar(self) -> bool:
        if self._ultimo_comando_monotonic == 0.0:
            return False
        return time.monotonic() - self._ultimo_comando_monotonic < TIMEOUT_INATIVIDADE_MANUAL_S

    async def executar(self) -> None:
        await self._event_bus.publish("behavior.status", {"estado": "controle_manual"})
        logger.info("maestro: controle manual assumiu (dashboard)")
        try:
            while self.quer_rodar():
                await asyncio.sleep(0.3)
            # Operador soltou (sem comando novo por TIMEOUT_INATIVIDADE_MANUAL_S)
            # - devolve o controle a autonomia. O joystick já manda STOP ao
            # soltar (ver dashboard.js), então isto é rede de segurança para o
            # caso de o navegador travar/fechar sem enviar o release: garante
            # que o robô para mesmo sem depender só do timeout do Mega.
            await self._event_bus.publish("navigation.comando", {"acao": "HOLD"})
        except asyncio.CancelledError:
            logger.info("maestro: controle manual preemptado")
            raise
        finally:
            self._reavaliar()
