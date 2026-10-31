"""Boot Manager do Kernel (Cap 6 secao 4).

Orquestra a sequencia de inicializacao do ORION OS. Etapas que dependem de
hardware ou de fases futuras do projeto (deteccao do Raspberry/Arduino,
banco de dados, IA, Vision, Motion Core) ainda nao existem neste ponto
(Fase 1) - o Boot Manager tolera modulos ausentes: registra o que falta e
segue em frente. So aborta se a propria configuracao for invalida
(Cap 17 secao 2).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orion.kernel.config import ConfigurationManager
from orion.kernel.event_bus import EventBus, Prioridade
from orion.kernel.logger import configurar_logger
from orion.kernel.registry import EstadoModulo, ServiceRegistry
from orion.kernel.watchdog import HealthMonitor, Watchdog

if TYPE_CHECKING:  # so para type hints - ver import local mais abaixo
    from orion.communication.service import ComunicacaoService

VERSAO_KERNEL = "0.1.0"

#: tempo maximo esperando a conexao TCP com o Raspberry antes de desistir
#: e tolerar a ausencia (Cap 6 s.8) - sem isso, um Raspberry desligado ou
#: fora do alcance faria o boot travar esperando o timeout do proprio SO.
TIMEOUT_CONEXAO_RASPBERRY_S = 3.0

#: Por quanto tempo INSISTIR na conexao antes de tolerar a ausencia.
#:
#: POR QUE EXISTE (2026-09-20). Uma tentativa so bastava enquanto o Mission
#: Core era subido na mao, sempre depois do Motion Core ja estar de pe.
#: Desde que os dois viraram servico systemd no mesmo Pi, eles sobem JUNTOS,
#: e o Mission Core chega na porta antes de ela existir:
#:
#:     16:46:59  Raspberry inalcancavel em 127.0.0.1:5757: [Errno 111]
#:     ... e 98 "Sem rota conhecida para o destino 'motion_core'" depois
#:
#: `After=orion-motion.service` na unit NAO resolve isso: o systemd so espera
#: o processo NASCER, nao o socket abrir. Quem tem que ter paciencia e quem
#: disca. E a falha e silenciosa do jeito pior: o robo sobe, fala, responde -
#: e fica sem memoria nenhuma ate alguem reiniciar o servico na mao.
ESPERA_TOTAL_RASPBERRY_S = 30.0

#: Intervalo entre tentativas. Curto: o caso comum e o Motion Core estar a
#: segundos de abrir a porta, e cada segundo aqui e um segundo de boot.
INTERVALO_RETENTATIVA_RASPBERRY_S = 2.0

# Etapas do Cap 6 secao 4 que dependem de hardware/fases ainda nao
# implementadas neste projeto. Cada uma vira um evento diagnostic
# informativo no boot, em vez de travar a inicializacao. "raspberry" saiu
# desta lista - agora e uma tentativa de conexao de verdade (ver
# _conectar_raspberry), nao so um aviso.
_ETAPAS_PENDENTES = (
    ("hardware_core", "Deteccao do Hardware Core via Raspberry (Fase 2/4)"),
    ("database", "Banco de dados no SSD (Fase 3)"),
    ("ai", "IA / Ollama (Fase 6)"),
    ("motion_hardware", "Motion Core + Hardware Core (Fase 4/7)"),
)
# "vision" saiu desta lista em 2026-07-25 (EDR-0023): agora e um modulo de
# verdade, iniciado em _iniciar_modulos, e nao mais um aviso no log.


async def _conectar_raspberry(
    comm: ComunicacaoService,
    config: ConfigurationManager,
    event_bus: EventBus,
    logger: logging.Logger,
    simulado: bool = False,
    espera_total_s: float | None = None,
) -> bool:
    """Tenta conectar no Motion Core (Raspberry) via TCP (Cap 14 s.2) e
    confirma via WHO_ARE_YOU. Tolera ausencia (Cap 6 s.8): Raspberry
    desligado ou fora da rede nao aborta o boot do Notebook.

    `espera_total_s` sobrescreve ESPERA_TOTAL_RASPBERRY_S. Existe para a
    RECONEXAO (ver `_criar_reconexao_motion_core`) poder pedir uma tentativa
    so (`0.0`) e cuidar ela mesma do backoff, em vez de aninhar duas esperas
    - a de boot e a de reconexao - uma dentro da outra.

    Com `simulado` (--sim) NAO abre soquete nenhum. Antes de 2026-07-25 a
    flag era ignorada aqui e o modo "simulado" conectava no Pi real - o que
    alem de errado era perigoso: o Pi so aceita UM enlace chamado
    "mission_core" por vez, entao rodar o boot para testar ROUBAVA o enlace
    do processo de producao (EDR-0023 secao 5)."""
    if simulado:
        logger.info("Modo simulado: enlace com o Raspberry nao sera aberto")
        return False
    # import local, nao no topo do arquivo: orion.communication.* importa
    # orion.kernel.event_bus, e orion/kernel/__init__.py importa boot.py
    # de olhos fechados antes de mais nada - import no topo daria um
    # ciclo de import (achado real, quebrava toda importacao do pacote
    # orion.kernel).
    from orion.communication.discovery import ErroVersaoIncompativel, descobrir
    from orion.communication.service import ErroComunicacao
    from orion.communication.transport import ErroTransporte, TcpTransport

    conf_raspberry = config.secao("communication")["raspberry"]

    # Insiste ate ESPERA_TOTAL_RASPBERRY_S. Um TcpTransport NOVO por tentativa
    # de proposito: um transporte que ja falhou em conectar nao volta atras.
    espera = ESPERA_TOTAL_RASPBERRY_S if espera_total_s is None else espera_total_s
    limite = asyncio.get_running_loop().time() + espera
    tentativa = 0
    while True:
        tentativa += 1
        cliente = TcpTransport(conf_raspberry["host"], conf_raspberry["tcp_port"])
        try:
            await asyncio.wait_for(cliente.conectar(), timeout=TIMEOUT_CONEXAO_RASPBERRY_S)
            break
        except (TimeoutError, ErroTransporte) as erro:
            if asyncio.get_running_loop().time() >= limite:
                logger.warning(
                    "Raspberry (Motion Core) inalcancavel em %s:%d apos %d tentativas em %.0fs: %s",
                    conf_raspberry["host"],
                    conf_raspberry["tcp_port"],
                    tentativa,
                    espera,
                    erro,
                )
                await event_bus.publish(
                    "diagnostic.error",
                    {"modulo": "raspberry", "motivo": "inalcancavel", "detalhe": str(erro)},
                    prioridade=Prioridade.BAIXA,
                )
                return False
            # INFO e nao WARNING: no boot pelo systemd, nao achar a porta na
            # primeira tentativa e o ESPERADO, nao um defeito. O WARNING fica
            # para quando a paciencia acabar - que ai sim e noticia.
            logger.info(
                "Motion Core ainda nao atende em %s:%d (tentativa %d) - esperando %.0fs",
                conf_raspberry["host"],
                conf_raspberry["tcp_port"],
                tentativa,
                INTERVALO_RETENTATIVA_RASPBERRY_S,
            )
            await asyncio.sleep(INTERVALO_RETENTATIVA_RASPBERRY_S)

    comm.adicionar_link("motion_core", cliente)
    try:
        info = await descobrir(comm, "motion_core", event_bus, timeout_s=TIMEOUT_CONEXAO_RASPBERRY_S)
    except (ErroVersaoIncompativel, ErroComunicacao) as erro:
        logger.warning("Raspberry conectou mas nao respondeu WHO_ARE_YOU: %s", erro)
        await event_bus.publish(
            "diagnostic.error",
            {"modulo": "raspberry", "motivo": "sem_resposta_who_are_you", "detalhe": str(erro)},
            prioridade=Prioridade.BAIXA,
        )
        return False

    logger.info("Raspberry (Motion Core) conectado: %s v%s", info.nome, info.versao_modulo)
    return True


#: Backoff da reconexao em OPERACAO (nao do boot). Os mesmos numeros do
#: Motion Core reconectando o corpo - ver _criar_reconexao_hardware_core em
#: motion_core/__main__.py.
RECONEXAO_ESPERA_INICIAL_S = 2.0
RECONEXAO_ESPERA_MAXIMA_S = 30.0


def _criar_reconexao_motion_core(
    comm: ComunicacaoService,
    config: ConfigurationManager,
    event_bus: EventBus,
    logger: logging.Logger,
    heartbeat: Any,
    limite_perdidos: int | None,
):
    """Devolve o callback que o MonitorHeartbeat chama quando o Motion Core some.

    BUG REAL (2026-09-20): o link com o Motion Core era aberto UMA vez, no
    boot, e nunca mais. Caindo o link, `_resolver_link` passava a levantar
    `ErroComunicacao: Sem rota conhecida para o destino 'motion_core'` a cada
    consulta - 98 delas num dia so. E a falha e silenciosa do jeito pior: o
    robo continua falando, ouvindo e respondendo, **sem memoria nenhuma**, e
    nada na fala denuncia. So voltava reiniciando o servico na mao.

    Este e o espelho exato de `_criar_reconexao_hardware_core` no Motion Core
    (o corpo reconectando o ESP32). A maquina ja existia e ja era chamada -
    `_marcar_perdido` -> `_tentar_reconectar` -> callback de `monitorar()`.
    Faltava o callback, do lado do Mission Core. Mesmo padrao de codigo orfao
    que ja mordeu este projeto seis vezes.

    POR QUE INSISTE SOZINHO: `_marcar_perdido` e IDEMPOTENTE - dispara uma vez
    por perda e so rearma quando um heartbeat volta a chegar. Como o caso
    normal e o Motion Core ainda estar subindo no instante da primeira
    tentativa, um callback que tentasse uma vez e desistisse trocaria um jeito
    de nao reconectar por outro.
    """
    #: Uma tentativa por vez. Sem esta guarda, uma segunda perda declarada
    #: enquanto a primeira ainda insiste abriria um segundo laco, e os dois
    #: passariam a discar em duplicata - o Motion Core so aceita UM enlace
    #: chamado "mission_core" por vez (EDR-0023 s.5).
    em_curso: dict[str, asyncio.Task | None] = {"tarefa": None}

    async def _insistir() -> None:
        espera = RECONEXAO_ESPERA_INICIAL_S
        tentativa = 0
        while True:
            tentativa += 1
            # espera_total_s=0.0: UMA discagem por ciclo. O backoff e daqui,
            # nao de dentro - senao seriam duas esperas aninhadas.
            if await _conectar_raspberry(
                comm, config, event_bus, logger, espera_total_s=0.0
            ):
                logger.info("Motion Core de volta na tentativa %d", tentativa)
                # Re-registrar zera o relogio do HealthMonitor (monitorar e
                # idempotente de proposito) e mantem ESTE callback armado
                # para a proxima queda.
                heartbeat.monitorar(
                    "motion_core",
                    reconectar=_ao_perder,
                    heartbeats_perdidos_limite=limite_perdidos,
                )
                await event_bus.publish(
                    "comm.module_recovered", {"modulo": "motion_core"}, prioridade=Prioridade.ALTA
                )
                return
            logger.warning(
                "Motion Core ainda fora (tentativa %d) - tento de novo em %.0fs",
                tentativa,
                espera,
            )
            await asyncio.sleep(espera)
            espera = min(espera * 2, RECONEXAO_ESPERA_MAXIMA_S)

    async def _ao_perder() -> None:
        anterior = em_curso["tarefa"]
        if anterior is not None and not anterior.done():
            logger.debug("reconexao do Motion Core ja esta em curso")
            return
        logger.warning("Motion Core sumiu - comecando a insistir na reconexao")
        em_curso["tarefa"] = asyncio.create_task(_insistir())

    return _ao_perder


async def _iniciar_modulos(
    modulos: list[Any],
    registry: ServiceRegistry,
    event_bus: EventBus,
    logger: logging.Logger,
) -> list[Any]:
    """Sobe os modulos do Mission Core (EDR-0023 secao 2).

    Retorna SO os que subiram - sao esses que o SistemaOrion precisa
    desligar depois. Um modulo que falha ao iniciar e "ausente", nao
    "sistema quebrado" (Cap 6 s.8): o boot registra, avisa e segue.
    """
    ativos: list[Any] = []
    for modulo in modulos:
        registry.registrar(modulo.nome, VERSAO_KERNEL, servicos=[modulo.nome])
        try:
            await modulo.iniciar()
        except Exception as erro:  # noqa: BLE001 - qualquer falha = ausente
            registry.atualizar_estado(modulo.nome, EstadoModulo.DEGRADED)
            logger.warning("Modulo %s indisponivel: %s", modulo.nome, erro, exc_info=True)
            await event_bus.publish(
                "diagnostic.error",
                {"modulo": modulo.nome, "motivo": "falha_ao_iniciar", "detalhe": str(erro)},
                prioridade=Prioridade.ALTA,
            )
            continue
        registry.atualizar_estado(modulo.nome, EstadoModulo.RUNNING)
        ativos.append(modulo)
        logger.info("Modulo %s iniciado", modulo.nome)
    return ativos


def _montar_modulos(
    config: ConfigurationManager,
    event_bus: EventBus,
    comm: ComunicacaoService,
    simulado: bool,
) -> list[Any]:
    """Modulos do Mission Core, NA ORDEM DE INICIALIZACAO (EDR-0023 s.4).

    A ordem importa e nao e arbitraria:
      1. display - sobe em milissegundos, entao o usuario ve o avatar no ar
         enquanto o resto ainda carrega, em vez de encarar uma tela morta;
      2. mission - a IA precisa estar de pe antes de qualquer modulo comecar
         a fazer perguntas a ela;
      3. vision  - por ultimo porque carrega o modelo YOLO do disco, de longe
         o passo mais lento do boot.
    O desligamento percorre esta lista ao contrario (SistemaOrion.encerrar).

    Em modo simulado nada de hardware real e aberto (EDR-0023 s.5): a lista
    sai vazia ate existirem simuladores de verdade.
    """
    if simulado:
        return []
    from orion.display.modulo import ModuloDisplay
    from orion.mission.memory_client import MemoryClient as _MemoryClient
    from orion.mission.modulo import ModuloMissao
    from orion.vision.modulo import ModuloVisao
    from orion.voice.modulo import ModuloVoz

    conf_visao = config.secao("vision")
    missao = ModuloMissao(event_bus, comm, config.secao("ai"))

    return [
        ModuloDisplay(event_bus, conf_visao),
        missao,
        # A voz recebe o METODO da missao, nao o modulo: e aqui, no
        # orquestrador, que os dois se encontram. ModuloVoz continua sem
        # saber que ModuloMissao existe (EDR-0023 s.1) e o Boot Manager e o
        # unico lugar que conhece os dois lados.
        ModuloVoz(
            event_bus,
            config.secao("voice"),
            processar_comando=missao.processar,
        ),
        ModuloVisao(
            event_bus,
            conf_visao,
            conf_sentinela=config.secao("behavior").get("sentinela_visao"),
            # os rostos da familia moram no banco, no SSD do Raspberry - o
            # Notebook os consulta pelo enlace (regra 5 do ARQUITETURA.txt).
            carregar_conhecidos=lambda: _MemoryClient(comm).recall("pessoas"),
            comm=comm,
        ),
    ]


class SistemaOrion:
    """Handles vivos apos o boot - usados pelo chamador e no encerramento."""

    def __init__(
        self,
        config: ConfigurationManager,
        event_bus: EventBus,
        registry: ServiceRegistry,
        health_monitor: HealthMonitor,
        watchdog: Watchdog,
        logger: logging.Logger,
        tarefa_bus: asyncio.Task,
        tarefa_watchdog: asyncio.Task,
        comm: ComunicacaoService,
        raspberry_conectado: bool,
        modulos: list[Any] | None = None,
        heartbeat: Any | None = None,
        tarefa_heartbeat: asyncio.Task | None = None,
    ) -> None:
        self.modulos = list(modulos or [])
        #: None em modo simulado (--sim nao abre enlace nenhum).
        self.heartbeat = heartbeat
        self._tarefa_heartbeat = tarefa_heartbeat
        self.config = config
        self.event_bus = event_bus
        self.registry = registry
        self.health_monitor = health_monitor
        self.watchdog = watchdog
        self.logger = logger
        self.comm = comm
        self.raspberry_conectado = raspberry_conectado
        self._tarefa_bus = tarefa_bus
        self._tarefa_watchdog = tarefa_watchdog

    async def encerrar(self) -> None:
        """Desligamento seguro: para o watchdog e o event bus (Cap 6)."""
        self.logger.info("Iniciando desligamento seguro do ORION OS")
        # Modulos primeiro, na ORDEM INVERSA da inicializacao (EDR-0023 s.2):
        # eles seguram hardware (camera, microfone) e ainda podem querer
        # publicar no bus ao sair - se o bus parasse antes, o desligamento
        # deles ficaria pela metade.
        for modulo in reversed(self.modulos):
            try:
                await modulo.encerrar()
            except Exception:  # noqa: BLE001 - desligamento nunca aborta
                self.logger.warning(
                    "Falha ao encerrar o modulo %s", getattr(modulo, "nome", "?"), exc_info=True
                )
        self.watchdog.parar()
        # Antes do comm.encerrar(): parado, o monitor nao declara perda nem
        # dispara uma reconexao no meio do desligamento - que ficaria
        # discando para um link que acabamos de fechar de proposito.
        if self.heartbeat is not None:
            self.heartbeat.parar()
        await self.comm.encerrar()
        self.event_bus.parar()
        tarefas = [self._tarefa_watchdog, self._tarefa_bus]
        if self._tarefa_heartbeat is not None:
            tarefas.append(self._tarefa_heartbeat)
        for tarefa in tarefas:
            tarefa.cancel()
            try:
                await tarefa
            except asyncio.CancelledError:
                pass
        self.registry.atualizar_estado("mission_core", EstadoModulo.STOPPED)
        self.logger.info("ORION OS encerrado")


class BootManager:
    """Executa a sequencia de boot do Cap 6 secao 4."""

    def __init__(
        self,
        caminho_config: Path | str = "config/orion.yaml",
        caminho_log: Path | str = "data/logs/orion.log",
        simulado: bool = False,
    ) -> None:
        self._caminho_config = caminho_config
        self._caminho_log = caminho_log
        self._simulado = simulado

    async def iniciar(self) -> SistemaOrion:
        # 1. Carregar configuracao (aborta aqui se invalida - Cap 17 secao 2).
        config = ConfigurationManager(self._caminho_config).carregar()
        secao_sistema = config.secao("system")

        # 2. Inicializar Logger.
        logger = configurar_logger(
            "orion", nivel=secao_sistema["log_level"], arquivo_log=self._caminho_log
        )
        modo = "SIMULADO" if self._simulado else "REAL"
        logger.info("Boot iniciado - robot_name=%s modo=%s", secao_sistema["robot_name"], modo)

        # 3. Inicializar Event Bus.
        event_bus = EventBus()
        tarefa_bus = asyncio.create_task(event_bus.iniciar())

        # 4. Registrar Mission Core (este processo).
        registry = ServiceRegistry()
        registry.registrar("mission_core", VERSAO_KERNEL, servicos=["kernel"])
        registry.atualizar_estado("mission_core", EstadoModulo.RUNNING)

        # 5. Comunicacao (Cap 14) + deteccao do Raspberry Pi (Fase 2) - agora
        # implementada de verdade: tenta conectar via TCP e tolera ausencia
        # (Cap 6 secao 8) em vez de so logar "nao implementado". Import
        # local - ver comentario em _conectar_raspberry sobre o ciclo de
        # import evitado.
        from orion.communication.service import ComunicacaoService

        conf_comm = config.secao("communication")
        comm = ComunicacaoService(
            "mission_core",
            event_bus,
            max_retries=conf_comm["max_retries"],
            ack_timeout_ms=conf_comm["ack_timeout_ms"],
            versao_modulo=VERSAO_KERNEL,
        )
        raspberry_conectado = await _conectar_raspberry(
            comm, config, event_bus, logger, simulado=self._simulado
        )

        # Heartbeat com o Motion Core + RECONEXAO (2026-09-20). Ate aqui o
        # Mission Core montava HealthMonitor e Watchdog mas nenhum
        # MonitorHeartbeat: nao mandava heartbeat, nao percebia a queda do
        # enlace e nao reconectava. O outro lado sempre teve (o Motion Core
        # monitora "mission_core" e "hardware_core" desde 06/08) - a metade
        # que faltava era esta.
        heartbeat = None
        tarefa_heartbeat = None
        if not self._simulado:
            from orion.communication.heartbeat import MonitorHeartbeat

            limites_por_link = conf_comm.get("heartbeats_lost_threshold_por_link") or {}
            heartbeat = MonitorHeartbeat(
                comm,
                event_bus,
                intervalo_s=conf_comm["heartbeat_interval_s"],
                heartbeats_perdidos_limite=conf_comm["heartbeats_lost_threshold"],
            )
            # MONITORA MESMO SEM TER CONECTADO, de proposito. Se o Motion Core
            # demorar mais que a paciencia do boot, e so aqui que o robo ainda
            # tem chance de achar o corpo depois - o Motion Core aprendeu isto
            # em 06/08 com o hardware_core e vale igual deste lado.
            heartbeat.monitorar(
                "motion_core",
                reconectar=_criar_reconexao_motion_core(
                    comm,
                    config,
                    event_bus,
                    logger,
                    heartbeat,
                    limites_por_link.get("motion_core"),
                ),
                heartbeats_perdidos_limite=limites_por_link.get("motion_core"),
            )
            tarefa_heartbeat = asyncio.create_task(heartbeat.iniciar())

        # Modulos do Mission Core (EDR-0023). Vision e o primeiro; Voice,
        # Mission/IA e Display entram aqui conforme forem migrados de
        # tools/conversar_jarvis.py.
        modulos = await _iniciar_modulos(
            _montar_modulos(config, event_bus, comm, self._simulado), registry, event_bus, logger
        )

        # 6-10. Arduino (via Raspberry), banco, IA, Vision, Motion Core +
        # Hardware Core: ainda nao implementados nesta fase do projeto.
        # Tolerar modulo ausente em vez de abortar o boot (Cap 6 secao 8).
        for chave, descricao in _ETAPAS_PENDENTES:
            logger.info("Etapa de boot ainda nao implementada: %s", descricao)
            await event_bus.publish(
                "diagnostic.error",
                {"modulo": chave, "motivo": "nao_implementado", "descricao": descricao},
                prioridade=Prioridade.BAIXA,
            )

        # Health Monitor + Watchdog: prontos para monitorar modulos reais
        # assim que as fases seguintes os registrarem.
        health_monitor = HealthMonitor(
            intervalo_heartbeat_s=conf_comm["heartbeat_interval_s"],
            heartbeats_perdidos_limite=conf_comm["heartbeats_lost_threshold"],
        )
        watchdog = Watchdog(health_monitor, event_bus=event_bus)
        tarefa_watchdog = asyncio.create_task(watchdog.iniciar())

        # 11. Autotestes (minimo nesta fase: bus e registry respondendo).
        assert registry.existe("mission_core")
        logger.info("Autotestes basicos do Kernel: OK")

        # 12. Publicar system.ready.
        await event_bus.publish(
            "system.ready",
            {"robot_name": secao_sistema["robot_name"], "modo": modo},
            prioridade=Prioridade.CRITICA,
        )
        await event_bus.aguardar_fila_vazia()
        logger.info("system.ready publicado - boot concluido")

        return SistemaOrion(
            config=config,
            event_bus=event_bus,
            registry=registry,
            health_monitor=health_monitor,
            watchdog=watchdog,
            logger=logger,
            tarefa_bus=tarefa_bus,
            tarefa_watchdog=tarefa_watchdog,
            comm=comm,
            raspberry_conectado=raspberry_conectado,
            modulos=modulos,
            heartbeat=heartbeat,
            tarefa_heartbeat=tarefa_heartbeat,
        )
