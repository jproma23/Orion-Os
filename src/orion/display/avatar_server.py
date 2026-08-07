"""Servidor local do avatar (Cap 13 secao 3, 7).

Serve os arquivos estaticos do avatar e empurra eventos do Event Bus para
o navegador via Server-Sent Events. O avatar e um consumidor puro do Event
Bus (Cap 13 s.2) - nenhuma logica de decisao mora aqui, so retransmissao.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
from pathlib import Path

from aiohttp import web

from orion.display import versao as _versao
from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("orion.display.avatar_server")

DIRETORIO_ESTATICO = Path(__file__).parent / "static"

#: topicos do Event Bus repassados ao avatar (Cap 13 s.7: consome
#: system.*, motion.*, vision.*, voice.*, navigation.*, diagnostic.*)
TOPICOS_REPASSADOS = (
    "voice.status",
    # Legenda do que o FOFAO esta falando (2026-07-27). Os dois eventos ja
    # carregavam o texto da resposta ({"texto": ...}, voice_core.py) desde
    # sempre - so ninguem consumia. Nao confundir com mostrar o que o
    # Whisper ouviu do usuario, que e outra ideia.
    "voice.response_started",
    "voice.response_finished",
    # A SAUDACAO ESPONTANEA nunca chegou aqui (achado da varredura de
    # 2026-08-04). `saudacao.py` publica `voice.saudacao` ANTES de falar, e o
    # comentario de la diz, com todas as letras, "o avatar reage ao evento" -
    # so que o topico nunca esteve nesta lista. A legenda do cumprimento
    # jamais apareceu na tela, e publicar nao falha: nada denunciava.
    # Quarta vez que este projeto paga pelo mesmo defeito.
    "voice.saudacao",
    "motion.pan_tilt",
    "motion.status",
    "motion.obstacle_front",
    "diagnostic.error",
    "system.ready",
)

_INTERVALO_PING_S = 15.0


class AvatarServer:
    def __init__(
        self,
        event_bus: EventBus,
        config_frontend: dict[str, object],
        host: str = "127.0.0.1",
        port: int = 8090,
    ) -> None:
        self._event_bus = event_bus
        self._config_frontend = config_frontend
        self._host = host
        self._port = port
        self._clientes: set[web.StreamResponse] = set()
        self._runner: web.AppRunner | None = None
        #: Contadores de diagnostico. Existem para responder "o avatar cai
        #: sozinho ou o navegador desiste?" olhando o log, sem precisar
        #: reproduzir na frente da camera.
        self._conectados_total = 0
        self._eventos_enviados = 0
        #: Identificacao da build para a marca d'agua da tela. Lida do git
        #: UMA VEZ aqui, e nao a cada pedido: e informacao de subida, e
        #: chamar o git por requisicao seria trabalho por nada.
        #:
        #: Fica no AvatarServer, e nao no ModuloDisplay, porque ha DOIS
        #: caminhos que sobem o avatar - `python -m orion` (kernel/boot) e
        #: `tools/conversar_fofao.py`, que e o que roda em producao. Aqui os
        #: dois ganham sem fiacao nova.
        self._build = _versao.descobrir(
            pathlib.Path(__file__).resolve().parents[3],
            _versao.VERSAO_PRODUTO,
            _versao.AUTOR,
        )

        self._app = web.Application()
        self._app.router.add_get("/eventos", self._handler_sse)
        self._app.router.add_get("/config", self._handler_config)
        self._app.router.add_get("/", self._handler_index)
        self._app.router.add_static("/", DIRETORIO_ESTATICO, show_index=False)

        for topico in TOPICOS_REPASSADOS:
            event_bus.subscribe(topico, self._fazer_repassador(topico))

    async def _handler_index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(DIRETORIO_ESTATICO / "index.html")

    async def _handler_config(self, request: web.Request) -> web.Response:
        # Repassa parametros de config/orion.yaml (Cap 17) que o avatar no
        # navegador precisa (limites de pan/tilt) - nenhum valor fisico
        # fica fixo no JS (regra arquitetural #6).
        return web.json_response({**self._config_frontend, "build": self._build.como_dict()})

    async def _handler_sse(self, request: web.Request) -> web.StreamResponse:
        resposta = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await resposta.prepare(request)
        self._clientes.add(resposta)
        # Instrumentacao (2026-07-29). O dono relatou que a janela do kiosk
        # mostra "sem conexao" quando o Fofao termina de falar. Nao foi possivel
        # diagnosticar porque ESTE handler engolia o motivo: `except
        # (ConnectionResetError, CancelledError): pass`, sem uma linha de log.
        # De fora, "o navegador fechou a aba" e "a escrita falhou no meio de um
        # evento" eram indistinguiveis - e e a diferenca entre nao ter bug e
        # ter um.
        #
        # Mesma cegueira que ja custou tempo neste projeto (um `logger.debug`
        # escondeu a falha do SET_PAN_TILT). Aqui a saida NUNCA e silenciosa.
        self._conectados_total += 1
        inicio = time.monotonic()
        motivo = "cliente fechou"
        logger.info("avatar conectou (clientes agora: %d)", len(self._clientes))
        try:
            while True:
                await asyncio.sleep(_INTERVALO_PING_S)
                await resposta.write(b": ping\n\n")  # mantem a conexao viva
        except ConnectionResetError as erro:
            motivo = f"conexao resetada no ping ({erro})"
        except asyncio.CancelledError:
            # aiohttp cancela o handler quando o cliente vai embora - e o
            # caminho NORMAL de saida, nao um erro. Continua sendo engolido
            # (relancar derrubaria o desligamento do servidor), mas agora
            # aparece no log com quanto tempo durou.
            motivo = "handler cancelado (cliente saiu ou servidor parando)"
        except Exception as erro:  # noqa: BLE001
            # O caso que interessa: qualquer coisa que NAO estava previsto.
            # Antes, isto subia para o aiohttp, fechava a conexao e nao deixava
            # rastro nenhum.
            motivo = f"INESPERADO {type(erro).__name__}: {erro}"
            logger.warning("avatar caiu por excecao inesperada no ping", exc_info=True)
        finally:
            self._clientes.discard(resposta)
            logger.info(
                "avatar desconectou depois de %.1fs e %d eventos - %s "
                "(clientes restantes: %d)",
                time.monotonic() - inicio,
                self._eventos_enviados,
                motivo,
                len(self._clientes),
            )
        return resposta

    def _fazer_repassador(self, topico: str):
        async def _repassar(evento: Evento) -> None:
            linha = f"event: {topico}\ndata: {json.dumps(evento.dados)}\n\n".encode()
            # copia da lista de clientes: escrever e assincrono (cede o
            # loop), e um cliente pode conectar/desconectar entre uma
            # escrita e outra, mudando o set original durante a iteracao.
            for cliente in list(self._clientes):
                try:
                    await cliente.write(linha)
                    self._eventos_enviados += 1
                except (ConnectionResetError, RuntimeError) as erro:
                    self._clientes.discard(cliente)
                    logger.info(
                        "avatar descartado ao enviar '%s': %s: %s",
                        topico, type(erro).__name__, erro,
                    )
                except Exception:  # noqa: BLE001
                    # Sem esta clausula, um tipo de excecao fora da dupla acima
                    # subia para FORA do handler do Event Bus - e derrubava a
                    # entrega deste evento para os DEMAIS clientes, alem de nao
                    # deixar rastro. Um cliente ruim nao pode calar o avatar.
                    self._clientes.discard(cliente)
                    logger.warning(
                        "avatar descartado por excecao inesperada ao enviar '%s'",
                        topico, exc_info=True,
                    )

        return _repassar

    async def iniciar(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info("Avatar servido em http://%s:%d", self._host, self._port)

    async def encerrar(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
