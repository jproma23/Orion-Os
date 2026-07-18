"""Servidor local do avatar (Cap 13 secao 3, 7).

Serve os arquivos estaticos do avatar e empurra eventos do Event Bus para
o navegador via Server-Sent Events. O avatar e um consumidor puro do Event
Bus (Cap 13 s.2) - nenhuma logica de decisao mora aqui, so retransmissao.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import web

from orion.kernel.event_bus import Evento, EventBus

logger = logging.getLogger("orion.display.avatar_server")

DIRETORIO_ESTATICO = Path(__file__).parent / "static"

#: topicos do Event Bus repassados ao avatar (Cap 13 s.7: consome
#: system.*, motion.*, vision.*, voice.*, navigation.*, diagnostic.*)
TOPICOS_REPASSADOS = (
    "voice.status",
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
        return web.json_response(self._config_frontend)

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
        try:
            while True:
                await asyncio.sleep(_INTERVALO_PING_S)
                await resposta.write(b": ping\n\n")  # mantem a conexao viva
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._clientes.discard(resposta)
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
                except (ConnectionResetError, RuntimeError):
                    self._clientes.discard(cliente)

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
