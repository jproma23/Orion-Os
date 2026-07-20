"""Interface Web (Cap 13 s.4-5, 7) — servida pelo Raspberry Pi.

Diferente do avatar (Notebook, Cap 13 s.3), que e um repassador puro sem
estado, o Dashboard precisa mostrar o estado ATUAL do sistema pra quem
acabou de abrir a pagina - por isso este modulo mantem um pequeno cache em
memoria do ultimo valor de cada coisa relevante, atualizado a cada evento
(Cap 13 s.2: "nenhuma logica de DECISAO reside no Display Core" - guardar o
ultimo valor recebido nao e decisao, e so agregacao pra exibicao).

Varios dispositivos podem acessar ao mesmo tempo (Cap 13 s.4) - cada
conexao GET /eventos recebe os mesmos updates via Server-Sent Events.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from motion_core.memory.api import MemoryAPI
from orion.kernel.config import ConfigurationManager
from orion.kernel.event_bus import Evento, EventBus

logger = logging.getLogger("motion_core.webui.server")

DIRETORIO_ESTATICO = Path(__file__).parent / "static"

#: enderecos considerados "local" para a pagina CONFIGURACAO (Cap 13 s.4:
#: "acesso restrito") - a pedido do usuario, restrito ao proprio
#: Raspberry, nem o resto da rede local ve essa pagina.
ENDERECOS_LOCAIS = frozenset({"127.0.0.1", "::1"})

#: topicos consumidos (Cap 13 s.7: system.*, motion.*, vision.*, voice.*,
#: navigation.*, diagnostic.* - mais safety.*, que a Fusao de Sensores
#: passou a publicar - Cap 12 s.8/Cap 18 s.9)
TOPICOS_REPASSADOS = (
    "system.ready",
    "motion.status",
    "motion.position",
    "motion.scan_complete",
    "motion.obstacle_front",
    "navigation.mode_changed",
    "navigation.plan_created",
    "navigation.segment_started",
    "navigation.segment_completed",
    "navigation.obstacle_avoided",
    "navigation.target_lost",
    "navigation.error",
    "vision.person_detected",
    "voice.status",
    "diagnostic.error",
    "safety.safe_mode_entered",
    "safety.safe_mode_exited",
    "comm.mensagem.telemetry",
    "comm.link_degraded",
    "comm.module_lost",
    "comm.module_recovered",
    "memory.updated",
)

#: quantas conversas mostrar na pagina CONVERSA por padrao (Cap 13 s.4)
CONVERSAS_PADRAO = 30

#: quantos erros manter no painel de diagnostico (Cap 13 s.5)
MAXIMO_ERROS_RECENTES = 20
#: quantas linhas do fim do log estruturado (Cap 6 s.3/Fase 1) expor via
#: GET /log (Cap 13 s.4: "acesso ao log, somente leitura")
LINHAS_LOG_PADRAO = 200
CAMINHO_LOG = Path("data/logs/orion.log")

#: quantos eventos brutos manter no painel "ultimos eventos" (Cap 13 s.5)
MAXIMO_EVENTOS_RECENTES = 30

_INTERVALO_PING_S = 15.0


class WebUIServer:
    def __init__(
        self,
        event_bus: EventBus,
        host: str = "0.0.0.0",
        port: int = 8080,
        memory_api: MemoryAPI | None = None,
        config: ConfigurationManager | None = None,
    ) -> None:
        # memory_api e opcional (Cap 13 s.4: "consultas de historico...
        # locais ao SSD") - None quando o banco de dados nao esta
        # disponivel (ex.: SSD de producao nao montado nesta maquina de
        # dev), a pagina CONVERSA so mostra um aviso nesse caso em vez de
        # quebrar. config e opcional pela mesma razao - sem ele, a pagina
        # CONFIGURACAO so mostra um aviso.
        self._event_bus = event_bus
        self._host = host
        self._port = port
        self._memory_api = memory_api
        self._config = config
        self._clientes: set[web.StreamResponse] = set()
        self._runner: web.AppRunner | None = None

        # estado agregado (Cap 13 s.5): so o ultimo valor de cada coisa,
        # nunca logica de decisao - so cache pra quem acabou de conectar
        self._estado: dict[str, Any] = {
            "sistema": {"robot_name": None, "modo": None},
            "navegacao": {"modo": None, "ultimo_plano": None},
            "hardware": {
                "estado": None,
                "em_movimento": None,
                "distancia_frontal_cm": None,
                "distancia_traseira_cm": None,
                "temperatura_c": None,
                "umidade_percent": None,
                "inclinacao_graus": None,
                "aceleracao_g": None,
                "impacto_detectado": None,
            },
            "posicao": None,
            "seguranca": {"safe_mode_ativo": False, "motivo": None},
            "visao": {"ultima_deteccao": None},
            "voz": {"estado": None},
            "mapa": {"leituras": []},
            "diagnostico": {"ultimos_erros": [], "modulos": {}},
        }
        self._eventos_recentes: list[dict[str, Any]] = []

        self._app = web.Application()
        self._app.router.add_get("/eventos", self._handler_sse)
        self._app.router.add_get("/estado", self._handler_estado)
        self._app.router.add_get("/", self._handler_index)
        self._app.router.add_get("/mapa", self._handler_mapa)
        self._app.router.add_get("/diagnostico", self._handler_diagnostico)
        self._app.router.add_get("/log", self._handler_log)
        self._app.router.add_get("/conversa", self._handler_conversa)
        self._app.router.add_get("/api/conversas", self._handler_api_conversas)
        self._app.router.add_get("/configuracao", self._handler_configuracao)
        self._app.router.add_get("/api/configuracao", self._handler_api_configuracao)
        self._app.router.add_static("/", DIRETORIO_ESTATICO, show_index=False)

        for topico in TOPICOS_REPASSADOS:
            event_bus.subscribe(topico, self._fazer_manipulador(topico))

    async def _handler_index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(DIRETORIO_ESTATICO / "index.html")

    async def _handler_mapa(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(DIRETORIO_ESTATICO / "mapa.html")

    async def _handler_diagnostico(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(DIRETORIO_ESTATICO / "diagnostico.html")

    async def _handler_log(self, request: web.Request) -> web.Response:
        # Cap 13 s.4: "acesso ao log, somente leitura" - so le e devolve,
        # nunca aceita escrita/comando (regra arquitetural #1: nenhuma
        # logica mora no Display Core).
        if not CAMINHO_LOG.exists():
            return web.json_response({"linhas": [], "aviso": "nenhum log encontrado ainda"})
        try:
            linhas_pedidas = int(request.query.get("linhas", LINHAS_LOG_PADRAO))
        except ValueError:
            linhas_pedidas = LINHAS_LOG_PADRAO
        linhas_pedidas = max(1, min(linhas_pedidas, 2000))
        with CAMINHO_LOG.open("r", encoding="utf-8", errors="replace") as arquivo:
            todas_as_linhas = arquivo.readlines()
        return web.json_response({"linhas": todas_as_linhas[-linhas_pedidas:], "aviso": None})

    async def _handler_conversa(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(DIRETORIO_ESTATICO / "conversa.html")

    async def _handler_api_conversas(self, request: web.Request) -> web.Response:
        # Cap 13 s.4: "consultas de historico... locais ao SSD" - le direto
        # da memoria (Fase 3), sem passar pelo Event Bus, ja que o
        # historico completo nao vive em memoria RAM deste servidor.
        if self._memory_api is None:
            return web.json_response({"conversas": [], "aviso": "banco de dados nao disponivel"})
        try:
            limite = int(request.query.get("limite", CONVERSAS_PADRAO))
        except ValueError:
            limite = CONVERSAS_PADRAO
        limite = max(1, min(limite, 200))
        conversas = await self._memory_api.recall("conversas", limite=limite)
        return web.json_response({"conversas": list(reversed(conversas)), "aviso": None})

    @staticmethod
    def _acesso_local_permitido(request: web.Request) -> bool:
        return request.remote in ENDERECOS_LOCAIS

    async def _handler_configuracao(self, request: web.Request) -> web.StreamResponse:
        # Cap 13 s.4: "CONFIGURACAO ... acesso restrito" - a pedido do
        # usuario, restrito ao proprio Raspberry (nem o resto da rede
        # local ve essa pagina, diferente das outras 4).
        if not self._acesso_local_permitido(request):
            return web.Response(status=403, text="Acesso restrito ao proprio Raspberry.")
        return web.FileResponse(DIRETORIO_ESTATICO / "configuracao.html")

    async def _handler_api_configuracao(self, request: web.Request) -> web.Response:
        if not self._acesso_local_permitido(request):
            return web.json_response({"erro": "acesso restrito"}, status=403)
        if self._config is None:
            return web.json_response({"parametros": {}, "aviso": "configuracao nao disponivel"})
        return web.json_response({"parametros": self._config.bruto(), "aviso": None})

    async def _handler_estado(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"estado": self._estado, "eventos_recentes": self._eventos_recentes}
        )

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
                await resposta.write(b": ping\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._clientes.discard(resposta)
        return resposta

    def _fazer_manipulador(self, topico: str):
        async def _manipular(evento: Evento) -> None:
            dados = self._extrair_dados(topico, evento)
            self._atualizar_estado(topico, dados)
            self._registrar_evento_recente(topico, dados)
            await self._transmitir(topico, dados)

        return _manipular

    @staticmethod
    def _extrair_dados(topico: str, evento: Evento) -> dict[str, Any]:
        # TELEMETRY nao passa pela normalizacao de topico das EVENT (Cap 14
        # s.7) - o dado real fica aninhado em "payload" (mesmo detalhe ja
        # documentado em NavigationCore/FusaoSensores).
        if topico == "comm.mensagem.telemetry":
            return evento.dados.get("payload", {})
        return evento.dados

    def _atualizar_estado(self, topico: str, dados: dict[str, Any]) -> None:
        if topico == "system.ready":
            self._estado["sistema"] = {
                "robot_name": dados.get("robot_name"),
                "modo": dados.get("modo"),
            }
        elif topico == "navigation.mode_changed":
            self._estado["navegacao"]["modo"] = dados.get("modo")
        elif topico in (
            "navigation.plan_created",
            "navigation.segment_started",
            "navigation.segment_completed",
            "navigation.obstacle_avoided",
        ):
            self._estado["navegacao"]["ultimo_plano"] = {"evento": topico, **dados}
        elif topico == "motion.status":
            self._estado["hardware"]["estado"] = dados.get("estado")
        elif topico == "motion.position":
            self._estado["posicao"] = dados
        elif topico == "motion.scan_complete":
            self._estado["mapa"] = {"leituras": dados.get("leituras", [])}
        elif topico == "comm.mensagem.telemetry":
            hardware = self._estado["hardware"]
            for campo in (
                "estado",
                "em_movimento",
                "distancia_frontal_cm",
                "distancia_traseira_cm",
                "temperatura_c",
                "umidade_percent",
                "inclinacao_graus",
                "aceleracao_g",
                "impacto_detectado",
            ):
                if campo in dados:
                    hardware[campo] = dados[campo]
        elif topico == "safety.safe_mode_entered":
            self._estado["seguranca"] = {"safe_mode_ativo": True, "motivo": dados.get("motivo")}
        elif topico == "safety.safe_mode_exited":
            self._estado["seguranca"] = {"safe_mode_ativo": False, "motivo": None}
        elif topico == "vision.person_detected":
            self._estado["visao"]["ultima_deteccao"] = dados
        elif topico == "voice.status":
            self._estado["voz"]["estado"] = dados.get("estado")
        elif topico in ("diagnostic.error", "comm.link_degraded"):
            self._registrar_erro(topico, dados)
        elif topico == "comm.module_lost":
            self._atualizar_saude_modulo(dados.get("modulo"), "perdido")
        elif topico == "comm.module_recovered":
            self._atualizar_saude_modulo(dados.get("modulo"), "ok")

    def _registrar_erro(self, topico: str, dados: dict[str, Any]) -> None:
        erros = self._estado["diagnostico"]["ultimos_erros"]
        erros.append({"topico": topico, "dados": dados, "timestamp": time.time()})
        if len(erros) > MAXIMO_ERROS_RECENTES:
            erros.pop(0)

    def _atualizar_saude_modulo(self, nome: str | None, status: str) -> None:
        if not nome:
            return
        self._estado["diagnostico"]["modulos"][nome] = {"status": status, "timestamp": time.time()}

    def _registrar_evento_recente(self, topico: str, dados: dict[str, Any]) -> None:
        self._eventos_recentes.append({"topico": topico, "dados": dados, "timestamp": time.time()})
        if len(self._eventos_recentes) > MAXIMO_EVENTOS_RECENTES:
            self._eventos_recentes.pop(0)

    async def _transmitir(self, topico: str, dados: dict[str, Any]) -> None:
        linha = f"event: {topico}\ndata: {json.dumps(dados)}\n\n".encode()
        for cliente in list(self._clientes):
            try:
                await cliente.write(linha)
            except (ConnectionResetError, RuntimeError):
                self._clientes.discard(cliente)

    async def iniciar(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info("Interface web servida em http://%s:%d", self._host, self._port)

    async def encerrar(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
