"""Camada de servico do Communication Core (Cap 14 secao 7): comm.send,
comm.publish, comm.request, comm.status. Os modulos nunca acessam
transportes diretamente - so por meio desta classe.

Roteamento (Cap 14 secao 7): uma mensagem cujo destino nao e este modulo e
encaminhada automaticamente pelo link correto, com base no campo destino -
e assim que o Raspberry repassa mensagens entre o Notebook e o Hardware Core de
forma transparente, sem o Mission Core nem o Hardware Core saberem disso.
"""
from __future__ import annotations

import asyncio
import logging

from orion.communication.protocol import (
    TIPOS_QUE_EXIGEM_ACK,
    VERSAO_PROTOCOLO,
    ErroProtocoloInvalido,
    Mensagem,
    TipoMensagem,
)
from orion.communication.transport import ErroTransporte, Transporte
from orion.kernel.event_bus import EventBus, Prioridade
from orion.kernel.tarefas import disparar

logger = logging.getLogger("orion.communication.service")

#: destino especial usado por publish() para difundir um EVENT a todos os links
DESTINO_BROADCAST = "*"


class ErroComunicacao(Exception):
    """Falha de comunicacao (sem rota, sem ACK apos as retransmissoes, etc.)."""


class ComunicacaoService:
    """Implementa comm.send/publish/request/status sobre um ou mais Transporte.

    `nome_local` e o nome deste modulo no protocolo (ex.: "mission_core",
    "motion_core"). Cada link adicionado com `adicionar_link` representa uma
    conexao direta com um peer; `alcancaveis_via` declara quem mais pode ser
    alcancado atraves dele (ex.: o Raspberry alcanca "hardware_core" atraves
    do link serial com o Hardware Core).
    """

    def __init__(
        self,
        nome_local: str,
        event_bus: EventBus,
        max_retries: int = 3,
        ack_timeout_ms: int = 500,
        versao_modulo: str = "0.1.0",
    ) -> None:
        self._nome_local = nome_local
        self._event_bus = event_bus
        self._max_retries = max_retries
        self._ack_timeout_s = ack_timeout_ms / 1000
        self._versao_modulo = versao_modulo
        self._links: dict[str, Transporte] = {}
        self._rotas: dict[str, str] = {}
        self._porteiros: dict = {}
        self._ganchos_stop: dict = {}
        self._pendentes_ack: dict[str, asyncio.Future[Mensagem]] = {}
        self._pendentes_resposta: dict[str, asyncio.Future[Mensagem]] = {}
        # Uma tarefa de recepcao POR PEER (nao uma lista solta): ao
        # reconectar, precisamos achar e cancelar a tarefa do link antigo.
        # Com lista, as antigas ficavam rodando sobre sockets vivos - ver
        # _descartar_link_anterior.
        self._tarefas_recepcao: dict[str, asyncio.Task] = {}
        self._exigir_checksum_mensagem: dict[str, bool] = {}

    def adicionar_link(
        self,
        nome_peer: str,
        transporte: Transporte,
        alcancaveis_via: list[str] | None = None,
        exigir_checksum_mensagem: bool = True,
    ) -> None:
        """Registra um transporte conectado diretamente a `nome_peer` e
        comeca a consumir mensagens recebidas dele.

        `exigir_checksum_mensagem=False` desliga a validacao do campo
        "checksum" da mensagem para este link (usar no link serial com o
        Hardware Core): o firmware em C++ nao reproduz byte a byte a serializacao
        JSON canonica do Python, entao recalcular esse checksum no lado
        Python daria falso-negativo. A integridade do link serial ja e
        garantida pelo CRC16 da camada de enquadramento (Cap 14 s.3), que e
        um algoritmo simples e identico nas duas linguagens - por isso e
        seguro confiar nele e nao no checksum da mensagem para esse enlace.
        """
        self._descartar_link_anterior(nome_peer)

        self._links[nome_peer] = transporte
        self._rotas[nome_peer] = nome_peer
        self._exigir_checksum_mensagem[nome_peer] = exigir_checksum_mensagem
        for destino_indireto in alcancaveis_via or []:
            self._rotas[destino_indireto] = nome_peer
        self._tarefas_recepcao[nome_peer] = asyncio.create_task(
            self._loop_recepcao(nome_peer, transporte)
        )

    def _descartar_link_anterior(self, nome_peer: str) -> None:
        """Fecha de verdade o link anterior deste peer, se houver.

        Sem isto, reconectar VAZAVA socket e tarefa: `adicionar_link` so
        sobrescrevia a entrada do dicionario, e o transporte antigo
        continuava aberto com sua tarefa de recepcao rodando.

        E acontecia de verdade, nao em teoria: o link e declarado morto por
        HEARTBEAT ATRASADO, nao por socket fechado. Quando o Notebook
        engasgava de CPU, o socket seguia perfeitamente vivo - cada
        "reconexao" deixava mais uma conexao ESTABLISHED para tras (37
        observadas em 2026-07-19).
        """
        tarefa_antiga = self._tarefas_recepcao.pop(nome_peer, None)
        if tarefa_antiga is not None:
            tarefa_antiga.cancel()

        transporte_antigo = self._links.pop(nome_peer, None)
        if transporte_antigo is None:
            return

        # fechar() e assincrono e adicionar_link nao e - fecha em tarefa
        # propria. Erro ao fechar um socket ja morto e irrelevante aqui.
        async def _fechar() -> None:
            try:
                await transporte_antigo.fechar()
            except Exception:
                logger.debug("falha ao fechar link antigo '%s'", nome_peer, exc_info=True)

        disparar(_fechar(), f"fechar_link_{nome_peer}")
        logger.info("Link anterior com '%s' descartado (socket e tarefa fechados)", nome_peer)

    def definir_porteiro(self, destino: str, porteiro) -> None:
        """Todo COMMAND para `destino` passa antes por `porteiro(payload)`,
        que devolve None (segue) ou o motivo da recusa (2026-11-01).

        Vale para os dois caminhos: o que nasce aqui (`send`/`request`) e o
        que so passa por aqui (`_encaminhar`). Existe porque a checagem de
        obstaculo morava num modulo que a acao falada do Notebook nao
        atravessava - o roteamento transparente era um atalho para as rodas.
        """
        self._porteiros[destino] = porteiro

    def definir_gancho_stop(self, destino: str, gancho) -> None:
        """Ao REPASSAR um COMMAND STOP para `destino`, chama `gancho(mensagem)`
        (2026-11-10, auditoria C1-02/C5-01).

        O "Jarvis, para" nasce no Notebook e o Pi so o repassa ao corpo: a
        navegacao do Pi nem sabia, e FOLLOW/EXPLORE/Nav2 voltavam a mandar
        rodas em <=200 ms. O gancho avisa quem tem que parar junto. Fica aqui,
        e nao no porteiro, porque o porteiro tambem ve os STOP que o proprio
        Pi manda (a trava do LiDAR) - e esses nao sao pedido de ninguem.

        O gancho e SINCRONO e tem que voltar rapido: roda dentro da recepcao
        do link de origem. Excecao dele vira log, nunca queda da recepcao.
        """
        self._ganchos_stop[destino] = gancho

    def _motivo_recusa(self, destino: str, payload: dict) -> str | None:
        porteiro = self._porteiros.get(destino)
        if porteiro is None:
            return None
        try:
            return porteiro(payload)
        except Exception:
            # PORTEIRO QUE QUEBRA RECUSA (2026-11-10, auditoria C1-06/A3).
            # Um payload malformado do Notebook ("distancia_cm": "20 cm") fazia
            # o porteiro levantar ValueError dentro da recepcao do link, e a
            # recepcao morria calada: o STOP seguinte nunca chegava ao corpo.
            # Na duvida, comando que nao da para conferir nao anda.
            logger.exception("porteiro de '%s' falhou em %r - recusando", destino, payload)
            return "payload invalido"

    def _resolver_link(self, destino: str) -> Transporte:
        nome_peer = self._rotas.get(destino)
        if nome_peer is None:
            raise ErroComunicacao(f"Sem rota conhecida para o destino '{destino}'")
        return self._links[nome_peer]

    async def send(
        self, destino: str, payload: dict, tipo: TipoMensagem = TipoMensagem.COMMAND
    ) -> Mensagem:
        """comm.send: envia e aguarda ACK, com ate `max_retries` retransmissoes
        (Cap 14 secao 5). Publica comm.link_degraded se esgotar as tentativas."""
        if tipo is TipoMensagem.COMMAND:
            motivo = self._motivo_recusa(destino, payload)
            if motivo is not None:
                raise ErroComunicacao(f"Comando recusado antes de '{destino}': {motivo}")
        mensagem = Mensagem.nova(tipo, self._nome_local, destino, payload)
        transporte = self._resolver_link(destino)

        loop = asyncio.get_running_loop()
        futuro = loop.create_future()
        self._pendentes_ack[mensagem.id] = futuro
        try:
            for tentativa in range(1, self._max_retries + 1):
                try:
                    await transporte.enviar(mensagem.to_bytes())
                except ErroTransporte as erro:
                    # O contrato de send() para quem chama e ErroComunicacao
                    # (Cap 14 s.5). ErroTransporte e detalhe da camada de
                    # transporte e nenhum modulo de aplicacao deveria conhecer.
                    #
                    # Deixar ele vazar cru fazia o chamador que tratava
                    # ErroComunicacao CORRETAMENTE morrer mesmo assim - as duas
                    # herdam de Exception e nenhuma e subclasse da outra. Achado
                    # real (2026-07-27): com a serial caida no instante em que o
                    # FOLLOW perdia o alvo, a task de monitoramento morria em
                    # silencio e a navegacao ficava presa em FOLLOW para sempre.
                    raise ErroComunicacao(
                        f"Falha ao enviar para '{destino}': {erro}"
                    ) from erro
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(futuro), timeout=self._ack_timeout_s
                    )
                except TimeoutError:
                    logger.warning(
                        "ACK nao recebido de '%s' (tentativa %d/%d, msg_id=%s)",
                        destino,
                        tentativa,
                        self._max_retries,
                        mensagem.id,
                    )

            await self._event_bus.publish(
                "comm.link_degraded",
                {"destino": destino, "mensagem_id": mensagem.id},
                prioridade=Prioridade.ALTA,
            )
            raise ErroComunicacao(
                f"Sem ACK de '{destino}' apos {self._max_retries} tentativas (msg_id={mensagem.id})"
            )
        finally:
            self._pendentes_ack.pop(mensagem.id, None)

    async def publish(self, topico: str, payload: dict | None = None, local: bool = True) -> None:
        """comm.publish: difunde um EVENT a todos os links conectados e,
        se `local` (padrao), tambem no Event Bus local (Cap 14 secao 7).

        `local=False` encaminha um evento que ja existe no bus local para os
        peers SEM re-publica-lo aqui - evita laco infinito quando o proprio
        handler que reage ao evento e quem o encaminha (ex.: o Notebook
        repassando voice.* ao Pi)."""
        payload = payload or {}
        mensagem = Mensagem.nova(
            TipoMensagem.EVENT,
            self._nome_local,
            DESTINO_BROADCAST,
            {"topico": topico, **payload},
        )
        # `list(...)` NAO e decoracao (varredura de 2026-08-04): ha um `await`
        # dentro do laco, e `adicionar_link` mexe em `self._links` de outra
        # tarefa a cada reconexao. Iterar o dicionario vivo levanta
        # "dictionary changed size during iteration" no meio de um broadcast -
        # justamente quando o link esta instavel, que e quando o evento mais
        # importa.
        for nome_peer, transporte in list(self._links.items()):
            try:
                await transporte.enviar(mensagem.to_bytes())
            except ErroTransporte:
                logger.warning("Falha ao propagar evento '%s' via '%s'", topico, nome_peer)
        if local:
            await self._event_bus.publish(topico, payload)

    async def request(self, destino: str, payload: dict, timeout_s: float) -> Mensagem:
        """comm.request: envia um COMMAND e aguarda a RESPONSE correspondente
        (correlacionada por id_referencia), de forma sincrona ate `timeout_s`."""
        motivo = self._motivo_recusa(destino, payload)
        if motivo is not None:
            raise ErroComunicacao(f"Comando recusado antes de '{destino}': {motivo}")
        mensagem = Mensagem.nova(TipoMensagem.COMMAND, self._nome_local, destino, payload)
        transporte = self._resolver_link(destino)

        loop = asyncio.get_running_loop()
        futuro = loop.create_future()
        self._pendentes_resposta[mensagem.id] = futuro
        try:
            await transporte.enviar(mensagem.to_bytes())
            return await asyncio.wait_for(futuro, timeout=timeout_s)
        except ErroTransporte as erro:
            # Mesmo motivo do send(). Aqui o estrago era pior: o WHO_ARE_YOU da
            # descoberta passa por aqui, e `_conectar_hardware_core` promete no proprio
            # docstring que "nunca levanta excecao" - mas so pegava
            # ErroComunicacao. Se o Mega resetasse na janela entre conectar() e o
            # WHO_ARE_YOU, ErroTransporte subia ate o asyncio.run e MATAVA o
            # Motion Core no boot.
            raise ErroComunicacao(
                f"Falha ao enviar pedido para '{destino}': {erro}"
            ) from erro
        except TimeoutError as erro:
            raise ErroComunicacao(
                f"Sem resposta de '{destino}' em {timeout_s}s (msg_id={mensagem.id})"
            ) from erro
        finally:
            self._pendentes_resposta.pop(mensagem.id, None)

    async def responder(self, mensagem_original: Mensagem, payload: dict) -> None:
        """Envia uma RESPONSE referenciando `mensagem_original` de volta a sua origem."""
        resposta = Mensagem.nova(
            TipoMensagem.RESPONSE,
            self._nome_local,
            mensagem_original.origem,
            payload,
            id_referencia=mensagem_original.id,
        )
        transporte = self._resolver_link(mensagem_original.origem)
        await transporte.enviar(resposta.to_bytes())

    def status(self) -> dict[str, bool]:
        """comm.status: estado (conectado/desconectado) de cada link direto."""
        return {nome: transporte.conectado for nome, transporte in self._links.items()}

    async def enviar_heartbeat(self, destino: str) -> None:
        """Envia um HEARTBEAT a `destino` (Cap 14 secao 6). Nao exige ACK,
        entao nao passa pela logica de retransmissao de send()."""
        transporte = self._resolver_link(destino)
        mensagem = Mensagem.nova(TipoMensagem.HEARTBEAT, self._nome_local, destino)
        await transporte.enviar(mensagem.to_bytes())

    async def _loop_recepcao(self, nome_peer: str, transporte: Transporte) -> None:
        try:
            await self._consumir_mensagens(nome_peer, transporte)
        except (OSError, ErroTransporte):
            # Link caiu de forma abrupta (ex.: ConnectionResetError num TCP
            # derrubado sem aviso) - encerra so a recepcao deste peer, nao
            # derruba o service inteiro. O MonitorHeartbeat/Watchdog detecta
            # a perda e decide a recuperacao (Cap 6 secao 8; Cap 14 secao 6).
            logger.warning("Link com '%s' caiu (recepcao encerrada)", nome_peer)

    async def _consumir_mensagens(self, nome_peer: str, transporte: Transporte) -> None:
        async for bruto in transporte.receber():
            # UMA MENSAGEM RUIM NAO MATA O LINK (2026-11-10, auditoria A3).
            # Qualquer excecao que nao fosse OSError/ErroTransporte saia daqui,
            # passava reto pelo `_loop_recepcao` e encerrava a recepcao deste
            # peer sem uma linha de log - o Pi ficava surdo ao Notebook ate o
            # restart. Queda de link continua subindo (e o que ela significa).
            try:
                await self._processar_bruto(nome_peer, transporte, bruto)
            except (OSError, ErroTransporte):
                raise
            except Exception:
                logger.exception(
                    "Erro ao tratar mensagem de '%s' - descartada, recepcao segue", nome_peer
                )

    async def _processar_bruto(self, nome_peer: str, transporte: Transporte, bruto: bytes) -> None:
        try:
            mensagem = Mensagem.from_bytes(bruto)
        except ErroProtocoloInvalido:
            logger.warning("Mensagem malformada recebida de '%s', descartada", nome_peer)
            return

        exige_checksum = self._exigir_checksum_mensagem.get(nome_peer, True)
        if exige_checksum and not mensagem.checksum_valido():
            # CRC invalido -> descarte silencioso + NACK (Cap 14 secao 5)
            logger.warning("Checksum invalido em mensagem de '%s', enviando NACK", nome_peer)
            try:
                await transporte.enviar(
                    Mensagem.nack(mensagem, self._nome_local, "checksum_invalido").to_bytes()
                )
            except ErroTransporte:
                pass
            return

        await self._tratar_mensagem_recebida(nome_peer, transporte, mensagem)

    async def _tratar_mensagem_recebida(
        self, nome_peer: str, transporte: Transporte, mensagem: Mensagem
    ) -> None:
        if mensagem.destino not in (self._nome_local, DESTINO_BROADCAST):
            await self._encaminhar(mensagem)
            return

        if mensagem.tipo is TipoMensagem.ACK:
            futuro = self._pendentes_ack.get(mensagem.id_referencia or "")
            if futuro is not None and not futuro.done():
                futuro.set_result(mensagem)
            return

        if mensagem.tipo is TipoMensagem.NACK:
            futuro = self._pendentes_ack.get(mensagem.id_referencia or "")
            if futuro is not None and not futuro.done():
                futuro.set_exception(
                    ErroComunicacao(f"NACK recebido: {mensagem.payload.get('motivo')}")
                )
            return

        if mensagem.tipo is TipoMensagem.RESPONSE:
            futuro = self._pendentes_resposta.get(mensagem.id_referencia or "")
            if futuro is not None and not futuro.done():
                futuro.set_result(mensagem)
            return

        if mensagem.tipo in TIPOS_QUE_EXIGEM_ACK:
            try:
                await transporte.enviar(Mensagem.ack(mensagem, self._nome_local).to_bytes())
            except ErroTransporte:
                logger.warning("Falha ao enviar ACK para '%s'", nome_peer)

        if mensagem.tipo is TipoMensagem.COMMAND and mensagem.payload.get("comando") == "WHO_ARE_YOU":
            # Descoberta de dispositivos (Cap 14 secao 8): todo modulo
            # responde com nome, versao propria e versao de protocolo.
            await self.responder(
                mensagem,
                {
                    "nome": self._nome_local,
                    "versao_modulo": self._versao_modulo,
                    "versao_protocolo": VERSAO_PROTOCOLO,
                },
            )
            return

        if mensagem.tipo is TipoMensagem.EVENT:
            topico = mensagem.payload.get("topico")
            dados = {k: v for k, v in mensagem.payload.items() if k != "topico"}
            if topico:
                await self._event_bus.publish(topico, dados)
                return

        await self._event_bus.publish(
            f"comm.mensagem.{mensagem.tipo.value.lower()}", mensagem.to_dict()
        )

    async def _encaminhar(self, mensagem: Mensagem) -> None:
        """Roteamento transparente: reenvia para quem sabe chegar em `mensagem.destino`."""
        # O gancho do STOP vem ANTES do envio, e vale mesmo sem rota ou com o
        # envio falhando: a parte sincrona dele (`NavigationCore.parar_ja`)
        # segura a navegacao na hora, entao nenhum CMD_VEL dela sai entre o
        # STOP repassado e o aviso - e quem anda tem que parar mesmo que o
        # enlace com o corpo esteja caido agora.
        self._avisar_stop_repassado(mensagem)
        if mensagem.tipo is TipoMensagem.COMMAND:
            motivo = self._motivo_recusa(mensagem.destino, mensagem.payload)
            if motivo is not None:
                logger.warning(
                    "Comando de '%s' para '%s' recusado: %s",
                    mensagem.origem,
                    mensagem.destino,
                    motivo,
                )
                try:
                    volta = self._resolver_link(mensagem.origem)
                    await volta.enviar(Mensagem.nack(mensagem, self._nome_local, motivo).to_bytes())
                except (ErroComunicacao, ErroTransporte):
                    logger.warning("Nao consegui avisar '%s' da recusa", mensagem.origem)
                return
        try:
            transporte = self._resolver_link(mensagem.destino)
        except ErroComunicacao:
            logger.warning(
                "Sem rota para encaminhar mensagem destino='%s' origem='%s'",
                mensagem.destino,
                mensagem.origem,
            )
            return
        # Reassina o checksum antes de reenviar: o firmware C++ nao reproduz
        # a serializacao JSON canonica do Python, entao o checksum original
        # de uma mensagem vinda do serial nunca validaria no proximo enlace
        # TCP (o Notebook rejeitava com NACK toda resposta do Arduino
        # encaminhada pelo Raspberry). A integridade do enlace de entrada ja
        # foi garantida ao receber (checksum da mensagem no TCP; CRC16 do
        # enquadramento no serial) - o roteador pode assinar pela mensagem.
        mensagem.checksum = mensagem.checksum_esperado()
        try:
            await transporte.enviar(mensagem.to_bytes())
        except ErroTransporte:
            logger.warning("Falha ao encaminhar mensagem para '%s'", mensagem.destino)

    def _avisar_stop_repassado(self, mensagem: Mensagem) -> None:
        if mensagem.tipo is not TipoMensagem.COMMAND or mensagem.payload.get("comando") != "STOP":
            return
        gancho = self._ganchos_stop.get(mensagem.destino)
        if gancho is None:
            return
        try:
            gancho(mensagem)
        except Exception:
            logger.exception("gancho do STOP repassado para '%s' falhou", mensagem.destino)

    async def encerrar(self) -> None:
        """Desligamento seguro: fecha os links primeiro, so depois para as
        tarefas de recepcao.

        BUG REAL (2026-07-27, vistoria pos-gravacao de firmware): a ordem
        era invertida - cancelar+esperar a tarefa de recepcao PRIMEIRO,
        fechar o transporte DEPOIS. Funciona para TCP (StreamReader.read()
        e nativo do asyncio, `task.cancel()` interrompe de verdade), mas
        NAO para a serial: `SerialTransport.receber()` le com
        run_in_executor (pyserial e sincrono, ver transport.py), e
        `task.cancel()` NAO interrompe uma chamada ja em andamento numa
        thread de executor - so passa a valer quando aquele read() retornar
        sozinho. Com o read() preso, o `await tarefa` do primeiro loop
        travava para sempre, e o codigo nunca chegava em
        `transporte.fechar()` (que e quem chama cancel_read() e destrava
        de verdade). Em producao isso travou o desligamento gracioso do
        Motion Core por >80s, so resolvido pelo SIGKILL do systemd.
        Fechar primeiro garante que a leitura pendente e interrompida antes
        de esperarmos a tarefa terminar - seguro tambem para TCP (fechar o
        socket com uma leitura pendente e o caminho normal de
        `_consumir_mensagens`/`_loop_recepcao`, que ja trata OSError/
        ErroTransporte como link caido)."""
        for transporte in self._links.values():
            await transporte.fechar()
        tarefas = list(self._tarefas_recepcao.values())
        for tarefa in tarefas:
            tarefa.cancel()
        for tarefa in tarefas:
            try:
                await tarefa
            except asyncio.CancelledError:
                pass
