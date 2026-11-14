"""Motion Core — processo principal do Raspberry Pi (Cap 6, 12, 13, 14).

Ponto de entrada real do Motion Core, equivalente ao `python -m orion` do
lado do Notebook (Mission Core): sobe o servidor TCP que o Notebook
conecta, a ponte serial com o Arduino (Hardware Core), os módulos de
navegação/fusão de sensores (Cap 12) e a interface web (Cap 13 s.4) -
tudo compartilhando um único Event Bus.

Tolera módulos/dispositivos ausentes em vez de abortar (Cap 6 s.8): sem
Arduino conectado, sem SSD montado ou sem o Notebook plugado, o processo
sobe do mesmo jeito e cada parte que depende do que falta fica desativada
(logada como diagnostic.error), igual ao Boot Manager do Kernel já faz.

Uso:
    python -m motion_core
"""
from __future__ import annotations

import asyncio
import logging
import math
import signal
import time
from pathlib import Path

from motion_core.behavior import contrato
from motion_core.behavior.behavior_core import MODO_AUTONOMO, MODO_MANUAL, BehaviorCore
from motion_core.behavior.comportamentos import (
    AcompanharPessoa,
    Atender,
    AtenderGesto,
    ControleManual,
    IaEstrategica,
    Repouso,
    Ronda,
    VigilanciaObstaculo,
    Vigilia,
)
from motion_core.behavior.estado_do_mundo import EstadoDoMundo
from motion_core.behavior.guardiao_ram import GuardiaoRamNotebook
from motion_core.behavior.instintos import InstintoBussola
from motion_core.behavior.memoria_decisoes import MemoriaDeDecisoes
from motion_core.behavior.ponte_conselho import PonteConselhoIA
from motion_core.desligamento import Desligador
from motion_core.lidar.gerenciador import LidarManager
from motion_core.memory.api import MemoryAPI
from motion_core.memory.bridge import PonteMemoria
from motion_core.memory.database import DatabaseManager
from motion_core.memory.experiencias import RegistradorExperiencias
from motion_core.memory.manutencao import TarefaManutencao
from motion_core.memory.replica_pendente import (
    FilaReplicaPendente,
    ReplicadorAutoRegenerativo,
)
from motion_core.memory.vault import VaultConhecimento
from motion_core.navigation.fusao_sensores import FusaoSensores
from motion_core.navigation.navigation_core import NavigationCore
from motion_core.navigation.trava_lidar import TravaLidar
from motion_core.navigation.gerente_nav2 import GerenteNav2
from motion_core.navigation.nav2_goal import Nav2Goal
from motion_core.vision.engine import CLASSES_PESSOAS_ANIMAIS, VisionEngine
from motion_core.webui.server import ORIGEM_OPERADOR, WebUIServer
from orion.communication.discovery import ErroVersaoIncompativel, descobrir
from orion.communication.heartbeat import MonitorHeartbeat
from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.communication.transport import (
    ConexaoTcp,
    ErroTransporte,
    SerialTransport,
    iniciar_servidor_tcp,
)
from orion.kernel.config import ConfigurationManager
from orion.kernel.event_bus import EventBus, Evento, Prioridade
from orion.kernel.logger import configurar_logger
from orion.kernel.registry import EstadoModulo, ServiceRegistry
from orion.mission.alivio_carga import TOPICO_PEDIDO_ALIVIO, TOPICO_RECUPERADO
from orion.mission.conselho_protocolo import TOPICO_PEDIDO
from orion.mission.diario import DiarioObservacoes
from orion.mission.grounding import montar_contexto

VERSAO_MOTION_CORE = "0.1.0"
NOME_MODULO = "motion_core"

#: "auto" em communication.hardware_core.port (Cap 17) = tentar estas portas em
#: ordem ate uma responder ao WHO_ARE_YOU. O CH340 do Mega re-enumera entre
#: ttyUSB0/ttyUSB1 quando o USB e replugado ou ha varias reconexoes (visto
#: 2026-07-19) - varrer a lista evita "Arduino sumiu" por causa disso.
PORTAS_SERIAIS_CANDIDATAS = ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0")

#: Espera entre tentativas de reconexao da serial, em segundos.
#:
#: POR QUE ELA CRESCE: abrir a porta serial ATIVA O DTR E RESETA O ESP32
#: (ver SerialTransport.__init__). Insistir de fracao em fracao de segundo
#: deixaria a placa em reset perpetuo no instante em que o cabo voltasse -
#: o robo pareceria continuar morto justamente porque estamos tentando
#: demais. O teto e baixo o bastante para ele voltar sozinho enquanto
#: alguem ainda esta na bancada.
RECONEXAO_ESPERA_INICIAL_S = 2.0
RECONEXAO_ESPERA_MAXIMA_S = 30.0


async def _tentar_porta(
    comm: ComunicacaoService,
    porta: str,
    baud_rate: int,
    event_bus: EventBus,
    logger: logging.Logger,
) -> bool:
    """Tenta abrir UMA porta e confirmar o Hardware Core por WHO_ARE_YOU."""
    transporte = SerialTransport(porta, baud_rate=baud_rate)
    try:
        await transporte.conectar()
    except ErroTransporte:
        return False

    comm.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)
    try:
        await descobrir(comm, "hardware_core", event_bus, timeout_s=3.0)
    except ErroVersaoIncompativel as erro:
        # NAO e "nao achei o corpo": achei, e o firmware e de outra versao do
        # protocolo (auditoria A6). Sem esta linha o sintoma era o mesmo de
        # cabo solto e ninguem pensava em regravar.
        logger.error("%s em %s - REGRAVAR o firmware do ESP32", erro, porta)
        return False
    except ErroComunicacao:
        # deixa o link registrado (comm.encerrar fecha o transporte no fim);
        # uma proxima candidata que responda sobrescreve "hardware_core".
        return False

    logger.info("Hardware Core (ESP32) conectado e confirmado em %s", porta)
    return True


async def _conectar_corpo_websocket(
    comm: ComunicacaoService,
    config_hardware_core: dict,
    event_bus: EventBus,
    logger: logging.Logger,
) -> bool:
    """Registra o corpo WebSocket como o link "hardware_core".

    SEM WHO_ARE_YOU: este firmware nao conhece o handshake do projeto (e
    nao conhece nada do protocolo - ver ponte_corpo_ws.py). Quem prova que
    ha alguem vivo do outro lado e a TELEMETRIA, e `conectar()` so volta
    depois do primeiro quadro. Um socket aberto para um firmware travado
    nao passa por aqui.
    """
    from motion_core.navigation.ponte_corpo_ws import TransporteCorpoWS

    url = config_hardware_core["websocket_url"]
    transporte = TransporteCorpoWS(
        url,
        pulsos_por_metro=float(config_hardware_core["pulsos_por_metro"]),
        pulsos_por_grau=float(config_hardware_core["pulsos_por_grau"]),
    )
    try:
        await transporte.conectar()
    except Exception as erro:
        logger.warning("Corpo (WebSocket) inalcancavel em %s: %s", url, erro)
        await event_bus.publish(
            "diagnostic.error",
            {"modulo": "hardware_core", "motivo": "websocket_indisponivel", "url": url},
            prioridade=Prioridade.ALTA,
        )
        return False

    # exigir_checksum_mensagem=False, igual a serial: os quadros nascem aqui
    # dentro, nao atravessam meio fisico nenhum antes do comm os ler.
    comm.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)
    # `transporte._url` e o que RESPONDEU, que pode nao ser o primeiro da
    # lista - e saber se veio pelo nome ou pelo IP e o que diz, de graca, se
    # o mDNS esta de pe.
    logger.info("Hardware Core (ESP32) conectado por WebSocket em %s", transporte._url)
    return True


async def _conectar_hardware_core(
    comm: ComunicacaoService,
    config_hardware_core: dict,
    event_bus: EventBus,
    logger: logging.Logger,
) -> bool:
    """Abre o link com o Hardware Core. Retorna False (tolerado, Cap 6 s.8)
    se o corpo nao responder - nunca levanta excecao.

    DOIS CAMINHOS desde 2026-09-20, escolhidos por `transporte` na config:
    "serial" (protocolo binario, historico) e "websocket" (o ESP32 que esta
    no robo hoje, regravado com firmware WiFi). Bifurcar AQUI, e nao no
    chamador, e o que faz o boot e a RECONEXAO usarem o mesmo caminho - esta
    funcao e chamada nos dois lugares."""
    if config_hardware_core.get("transporte") == "websocket":
        return await _conectar_corpo_websocket(
            comm, config_hardware_core, event_bus, logger
        )

    porta = config_hardware_core["port"]
    candidatas = PORTAS_SERIAIS_CANDIDATAS if porta == "auto" else (porta,)

    for candidata in candidatas:
        if await _tentar_porta(
            comm, candidata, config_hardware_core["baud_rate"], event_bus, logger
        ):
            return True

    logger.warning("Hardware Core nao encontrado em nenhuma porta: %s", ", ".join(candidatas))
    await event_bus.publish(
        "diagnostic.error",
        {"modulo": "hardware_core", "motivo": "serial_indisponivel", "portas": list(candidatas)},
        prioridade=Prioridade.ALTA,
    )
    return False


def _criar_reconexao_hardware_core(
    comm: ComunicacaoService,
    config_hardware_core: dict,
    event_bus: EventBus,
    logger: logging.Logger,
    heartbeat: MonitorHeartbeat,
    limite_perdidos: int | None,
):
    """Devolve o callback que o MonitorHeartbeat chama quando o corpo some.

    BUG REAL (2026-08-06, relato do dono: *"quando tira o esp do usb trava
    tudo"*): `_conectar_hardware_core` so era chamada UMA vez, no boot.
    Tirado o cabo, `SerialTransport.receber()` levantava SerialException,
    o `_loop_recepcao` a tratava como link caido (certo, nao derruba o
    processo) e ninguem reconectava - a cabeca parava, a telemetria parava
    e o corpo so voltava reiniciando o servico.

    A maquina de reconexao ja existia e ja era chamada: `_marcar_perdido`
    -> `_tentar_reconectar` -> callback registrado em `monitorar()`.
    Faltava o callback. E o quinto caso de codigo orfao do projeto.

    POR QUE ELE INSISTE SOZINHO em vez de esperar ser chamado de novo:
    `_marcar_perdido` e IDEMPOTENTE - dispara uma vez por perda e so
    rearma quando um heartbeat volta a chegar do ESP32. Como o caso normal
    e o cabo continuar fora no instante da primeira tentativa, um callback
    que tentasse uma vez e desistisse deixaria o robo sem corpo para
    sempre - trocando um jeito de nao reconectar por outro.
    """
    #: Uma tentativa por vez. Sem esta guarda, uma segunda perda declarada
    #: enquanto a primeira ainda insiste abriria um segundo laco, e os dois
    #: passariam a resetar o ESP32 em duplicata.
    em_curso: dict[str, asyncio.Task | None] = {"tarefa": None}

    async def _insistir() -> None:
        espera = RECONEXAO_ESPERA_INICIAL_S
        tentativa = 0
        while True:
            tentativa += 1
            if await _conectar_hardware_core(comm, config_hardware_core, event_bus, logger):
                logger.info("Hardware Core de volta na tentativa %d", tentativa)
                # Re-registrar zera o relogio do HealthMonitor (ver
                # MonitorHeartbeat.monitorar, que e idempotente de proposito)
                # e mantem ESTE callback armado para a proxima queda.
                heartbeat.monitorar(
                    "hardware_core",
                    reconectar=_ao_perder,
                    heartbeats_perdidos_limite=limite_perdidos,
                )
                return
            logger.warning(
                "Hardware Core ainda fora (tentativa %d) - tento de novo em %.0fs",
                tentativa,
                espera,
            )
            await asyncio.sleep(espera)
            espera = min(espera * 2, RECONEXAO_ESPERA_MAXIMA_S)

    async def _ao_perder() -> None:
        anterior = em_curso["tarefa"]
        if anterior is not None and not anterior.done():
            logger.debug("reconexao do Hardware Core ja esta em curso")
            return
        logger.warning("Hardware Core sumiu - comecando a insistir na reconexao")
        em_curso["tarefa"] = asyncio.create_task(_insistir())

    return _ao_perder


def _abrir_memoria(config: ConfigurationManager, event_bus: EventBus, logger: logging.Logger):
    """Abre o banco de dados (Fase 3) se o SSD de producao estiver montado
    (Cap 15). Tolera ausencia - so a pagina CONVERSA fica sem historico."""
    conf_db = config.secao("database")
    caminho_db = Path(conf_db["path"])
    if not caminho_db.parent.exists():
        logger.warning(
            "SSD de producao nao montado (%s) - banco de dados indisponivel, "
            "pagina CONVERSA da interface web ficara so com o aviso",
            caminho_db.parent,
        )
        return None, None, None
    try:
        db = DatabaseManager(conf_db["path"], conf_db["backup_dir"])
        db.iniciar()
    except Exception:
        logger.exception("Falha ao abrir o banco de dados em %s", conf_db["path"])
        return None, None, None
    logger.info("Banco de dados conectado em %s", conf_db["path"])

    vault = None
    if conf_db.get("obsidian_vault_dir"):
        vault = VaultConhecimento(conf_db["obsidian_vault_dir"])
        logger.info("Vault de conhecimento aberto em %s", conf_db["obsidian_vault_dir"])

    return MemoryAPI(db, event_bus), db, vault


def _iniciar_manutencao(
    db: DatabaseManager,
    event_bus: EventBus,
    comm: ComunicacaoService,
    conf_db: dict,
    logger: logging.Logger,
) -> TarefaManutencao:
    """Agenda o backup diario (Cap 15 s.5) e, se configurado, replica o
    backup para o Notebook (Cap 15 s.6) assim que ele terminar.

    Auto-regeneracao (2026-08-16): se o Notebook (Mission Core) estiver
    dormindo na hora do backup, a replica NAO se perde - o caminho entra
    numa fila persistente e e reenviado sozinho assim que o Notebook
    voltar (evento `comm.module_recovered` do heartbeat). Nada do robô
    depende de o Notebook estar acordado na hora certa."""
    conf_retencao = conf_db["retention"]
    tarefa = TarefaManutencao(
        db,
        event_bus,
        hora_backup=conf_db["backup_hour"],
        retencao_kwargs={
            "telemetria_dias": conf_retencao["telemetria_days"],
            "eventos_dias": conf_retencao["eventos_days"],
            "logs_dias": conf_retencao["logs_days"],
            "logs_erro_dias": conf_retencao["logs_error_days"],
        },
    )

    if conf_db.get("replica_to_notebook"):
        # Fila persistente: sobrevive a reinicios do Motion Core.
        fila = FilaReplicaPendente(Path(conf_db["backup_dir"]) / "replica_pendente.json")
        fila.carregar()
        replicador = ReplicadorAutoRegenerativo(event_bus, comm, fila)
        replicador.registrar()
        if fila.pendentes():
            logger.info(
                "Auto-regeneracao: %d replica(s) pendente(s) da ultima vez",
                len(fila.pendentes()),
            )

    return tarefa


async def principal() -> None:
    config = ConfigurationManager("config/orion.yaml").carregar()
    secao_sistema = config.secao("system")
    logger = configurar_logger(NOME_MODULO, nivel=secao_sistema["log_level"])
    logger.info("Motion Core: boot iniciado - robot_name=%s", secao_sistema["robot_name"])

    event_bus = EventBus()
    tarefa_bus = asyncio.create_task(event_bus.iniciar())

    registry = ServiceRegistry()
    registry.registrar(NOME_MODULO, VERSAO_MOTION_CORE, servicos=["navigation", "webui"])
    registry.atualizar_estado(NOME_MODULO, EstadoModulo.STARTING)

    conf_comm = config.secao("communication")
    comm = ComunicacaoService(
        NOME_MODULO,
        event_bus,
        max_retries=conf_comm["max_retries"],
        ack_timeout_ms=conf_comm["ack_timeout_ms"],
        versao_modulo=VERSAO_MOTION_CORE,
    )
    heartbeat = MonitorHeartbeat(
        comm,
        event_bus,
        intervalo_s=conf_comm["heartbeat_interval_s"],
        heartbeats_perdidos_limite=conf_comm["heartbeats_lost_threshold"],
    )

    # 1. Servidor TCP para o Notebook (Mission Core) conectar (Cap 14 s.2).
    conf_raspberry = conf_comm["raspberry"]

    # Tolerancia propria do link com o Notebook (ver config): ele roda IA
    # local e trava a CPU por dezenas de segundos sem estar quebrado.
    limites_por_link = conf_comm.get("heartbeats_lost_threshold_por_link") or {}

    async def _ao_conectar_notebook(conexao: ConexaoTcp) -> None:
        comm.adicionar_link("mission_core", conexao)
        heartbeat.monitorar(
            "mission_core",
            heartbeats_perdidos_limite=limites_por_link.get("mission_core"),
        )
        logger.info("Notebook (Mission Core) conectado via TCP")

    servidor_tcp = await iniciar_servidor_tcp(
        "0.0.0.0", conf_raspberry["tcp_port"], _ao_conectar_notebook
    )
    logger.info(
        "Servidor TCP ouvindo em 0.0.0.0:%d - esperando o Notebook conectar",
        conf_raspberry["tcp_port"],
    )

    # 2. Ponte serial com o Arduino (Cap 14 s.2) - tolera ausencia.
    # O retorno nao e mais guardado: desde 06/08 o heartbeat monitora o
    # hardware_core conectado ou nao, entao nao ha decisao a tomar aqui. O
    # proprio _conectar_hardware_core ja loga e publica diagnostic.error.
    await _conectar_hardware_core(comm, conf_comm["hardware_core"], event_bus, logger)
    # Monitorado SEMPRE, conectado ou nao (mudou em 2026-08-06). Antes so
    # entrava aqui se o boot achasse a porta, e quem ligasse o robo com o
    # ESP32 fora da tomada ficava sem corpo ate reiniciar o servico - o
    # mesmo sintoma de puxar o cabo depois. A carencia do `_marcar_perdido`
    # cobre quem nunca conectou: N falhas de envio seguidas e ai sim vira
    # perda, que e o que dispara a reconexao.
    #
    # Sem tolerancia extra DE PROPOSITO: este e o caminho da seguranca
    # reativa - descobrir tarde que o corpo sumiu e pior que um falso
    # alarme (Cap 18).
    heartbeat.monitorar(
        "hardware_core",
        reconectar=_criar_reconexao_hardware_core(
            comm,
            conf_comm["hardware_core"],
            event_bus,
            logger,
            heartbeat,
            limites_por_link.get("hardware_core"),
        ),
        heartbeats_perdidos_limite=limites_por_link.get("hardware_core"),
    )

    tarefa_heartbeat = asyncio.create_task(heartbeat.iniciar())

    # 3. Navegacao + Fusao de Sensores (Cap 12) - registradas mesmo sem
    # Arduino conectado; comandos so falhariam com ErroComunicacao na hora
    # de enviar, tratado normalmente pelo _ao_receber_comando existente.
    config_motion = config.secao("motion")
    config_navigation = config.secao("navigation")
    navigation = NavigationCore(event_bus, comm, config_motion, config_navigation)
    # TRAVA DO LIDAR (2026-11-01): porteiro do enlace com as rodas. Todo
    # comando que anda - do Notebook, da webui, do maestro ou do Nav2 - so
    # chega ao ESP32 se o corredor a frente (ou atras) estiver livre. Ver
    # motion_core/navigation/trava_lidar.py.
    conf_trava = config_motion.get("trava_lidar", {})
    chassi = config.secao("chassi") or {}
    raio_seguranca_m = float(chassi.get("raio_seguranca_m", 0.25))
    piso_manual_cm = float(conf_trava.get("piso_manual_cm", raio_seguranca_m * 100.0))
    eixo_lidar_cm = 100.0 * float(chassi.get("lidar_ate_eixo_motriz_m", 0.10))
    corpo_frente_cm = 100.0 * float(chassi.get("lidar_recuo_m", 0.08))
    corpo_tras_cm = 100.0 * max(0.0, float(chassi.get("profundidade_m", 0.30)) - float(chassi.get("lidar_recuo_m", 0.08)))
    meia_largura_corpo_cm = 100.0 * float(chassi.get("largura_m", 0.39)) / 2.0
    canto_traseiro_eixo_cm = math.hypot(eixo_lidar_cm + corpo_tras_cm, meia_largura_corpo_cm)
    raio_giro_cm = float(conf_trava.get("raio_giro_cm", canto_traseiro_eixo_cm))

    # EDR-0037 (01/11 tarde) + Auditoria 10/11: BLOQUEIA por padrao.
    # 50 cm frente/tras no autonomo, piso de 25 cm no manual (raio_seguranca_m),
    # protecao de giro no lugar com raio em volta do eixo dianteiro (37.5 cm).
    trava_lidar = TravaLidar(
        event_bus,
        distancia_cm=float(conf_trava.get("distancia_cm", 50.0)),
        meia_largura_cm=float(conf_trava.get("meia_largura_cm", 25.0)),
        idade_max_s=float(conf_trava.get("idade_max_s", 0.6)),
        bloquear=bool(conf_trava.get("bloquear", True)),
        piso_manual_cm=piso_manual_cm,
        corpo_frente_cm=corpo_frente_cm,
        corpo_tras_cm=corpo_tras_cm,
        meia_largura_corpo_cm=meia_largura_corpo_cm,
        eixo_dist_cm=eixo_lidar_cm,
        raio_giro_cm=raio_giro_cm,
        cegas_para_parar=int(conf_trava.get("cegas_para_parar", 2)),
    )

    async def _parar_rodas() -> None:
        try:
            await comm.send("hardware_core", {"comando": "STOP"})
        except ErroComunicacao:
            logger.warning("trava do LiDAR: STOP nao chegou ao Hardware Core")

    trava_lidar.ligar_parada(_parar_rodas)
    # Watchdog: andando e o LiDAR calado alem de idade_max_s -> STOP.
    tarefa_watchdog_trava = asyncio.create_task(trava_lidar.vigiar())
    comm.definir_porteiro("hardware_core", trava_lidar.motivo_recusa)
    # STOP REPASSADO PARA A NAVEGACAO TAMBEM (2026-11-10, auditoria C1-02):
    # o "Jarvis, para" vem do Notebook e o Pi so o repassa ao corpo; sem isto
    # FOLLOW/EXPLORE/Nav2 voltavam a andar em <=200 ms. Gancho, e nao porteiro:
    # o porteiro ve tambem os STOP da propria trava acima.
    comm.definir_gancho_stop("hardware_core", lambda _mensagem: navigation.parar_ja("notebook"))
    FusaoSensores(event_bus, config_motion)
    # Forwarder de goal para o Nav2 (NaviCore): o Nav2 e quem resolve
    # COMO chegar (mapa, costmap, replanejamento); o Motion Core decide ONDE.
    # Tolera ausencia do container (falha vira navigation.goal_resultado).
    #
    # O gerente deixa o Nav2 DESLIGADO enquanto ninguem navega (~225 MB
    # medidos, num Pi de 3,8 GB) e o sobe no primeiro goal. Com
    # `nav2.sob_demanda: false` o gerente sai de cena e volta o modo antigo,
    # com o container de pe o tempo todo.
    conf_nav2 = config.secao("nav2")
    gerente_nav2 = None
    tarefa_gerente_nav2 = None
    if conf_nav2.get("sob_demanda"):
        gerente_nav2 = GerenteNav2(
            event_bus,
            ocioso_para_desligar_s=float(conf_nav2["ocioso_para_desligar_min"]) * 60.0,
        )
        tarefa_gerente_nav2 = asyncio.create_task(gerente_nav2.iniciar())
    Nav2Goal(event_bus, gerente=gerente_nav2)

    # 3c. LiDAR (Motion Core, USB serial) - publica setores no barramento para
    # a navegacao, o retrato do mundo e a web (decisao do dono 2026-08-17).
    #
    # AGORA E O UNICO SENSOR DE DISTANCIA DO ROBO (2026-09-19): os ultrassons
    # foram retirados fisicamente. O codigo continua tolerando ausencia (Cap 6
    # s.8) e nao aborta o boot, mas o que sobra sem ele NAO e mais "o ultrassom
    # frontal" - e nao ter medida nenhuma. `frente_desconhecida()` passa a
    # valer sempre, e quem decide andar tem que tratar isso como duvida, nunca
    # como caminho livre.
    try:
        conf_lidar = config.secao("lidar")
    except Exception:
        conf_lidar = {}
    lidar_manager = None
    if conf_lidar and conf_lidar.get("habilitado", True):
        lidar_manager = LidarManager(event_bus, conf_lidar)
        await lidar_manager.iniciar()
        # O nome do driver vem da CONFIG, nao fixo no texto: ate 16/09 esta
        # linha dizia "LiDAR LD06 ativo" com qualquer driver selecionado, e
        # log que mente o modelo manda quem procura defeito para o lado
        # errado. "ativo" aqui significa so "a task subiu" - quem confirma a
        # serial de verdade e o "LiDAR <driver> conectado em ..." do
        # gerenciador.
        logger.info(
            "LiDAR %s ativo (port=%s, setores=%d)",
            conf_lidar.get("driver", "ld06"),
            conf_lidar.get("port", "auto"),
            conf_lidar.get("setores", 12),
        )

    # 3b. Vision Engine do Pi (câmera dedicada: pessoas + animais, YOLO).
    #
    # O Motion Core é dono da câmera FÍSICA do robô (EDR-0023 s.3) e publica
    # detecções NO barramento do Pi com o ORION VISION PROTOCOL padronizado.
    # Tolera ausência total (Cap 6 s.8): sem câmera ou sem modelo, o robô
    # segue de pé e a visão só publica vision.camera_error.
    try:
        conf_vision = config.secao("vision")
    except Exception:
        conf_vision = {}
        logger.warning("secao `vision` ausente no orion.yaml - Vision Engine desligado")
    vision_engine = None
    # DESLIGADO POR PADRAO (2026-11-01). Desde 30/09 a camera (DV20) mora na
    # USB do Notebook e o stream do Pi (:8091) foi desligado: este engine
    # ficava publicando vision.camera_error a cada 3 s (5 em 15 s medidos),
    # e antes disso, com o ustreamer de pe, rodava o YOLO na tela "NO SIGNAL"
    # parecendo saudavel. Volta com vision.pi_engine_ativo: true quando houver
    # camera no Pi (o YOLO de reserva).
    if conf_vision and not conf_vision.get("pi_engine_ativo", False):
        logger.info("Vision Engine do Pi desligado (vision.pi_engine_ativo: false)")
    elif conf_vision:
        classes = conf_vision.get("pi_classes_interesse")
        if classes is None:
            classes = sorted(CLASSES_PESSOAS_ANIMAIS)
        # O YOLO chama as classes compostas com ESPACO ("cell phone"); a
        # lista do yaml usava sublinhado e as 15 compostas nunca casavam.
        classes = [str(c).replace("_", " ") for c in classes]
        vision_engine = VisionEngine(
            event_bus,
            camera_url=conf_vision.get("camera_url_pi", "http://127.0.0.1:8091/stream"),
            caminho_modelo=conf_vision.get("yolo_model_path", "data/modelos/yolov8n.pt"),
            confianca_minima=conf_vision.get("confidence_threshold", 0.55),
            imgsz=conf_vision.get("yolo_imgsz", 320),
            fps_alvo=conf_vision.get("fps_pi", 0.0),
            classes_interesse=frozenset(classes),
        )
        await vision_engine.iniciar()
        logger.info(
            "Vision Engine ativo (camera=%s, classes=%d)",
            conf_vision.get("camera_url_pi", "http://127.0.0.1:8091/stream"),
            len(classes),
        )

    # Guardião de RAM do Notebook (EDR-0020): o Pi vigia a memória do Notebook
    # e pede alívio antes do crash. Só um monitor de eventos - sem task própria.
    conf_guardiao = config.secao("behavior")["guardiao_ram"]
    GuardiaoRamNotebook(
        event_bus,
        limiar_critico_mb=conf_guardiao["limiar_critico_mb"],
        limiar_folga_mb=conf_guardiao["limiar_folga_mb"],
    )

    # Maestro (Behavior Core, EDR-0020): decide sozinho o que o robô faz.
    # Comeco enxuto - Repouso (base) e Vigilancia de obstaculo (prio maxima,
    # dispara com o OBSTACLE_DETECTED real do Mega).
    # Modelo de mundo: um retrato unico que o conselheiro de IA le para
    # opinar com o quadro inteiro em vez de sinais soltos.
    try:
        familia_conf = config.secao("familia")
    except Exception:
        familia_conf = []
        logger.warning("secao `familia` ausente no orion.yaml")

    # O aprendizado de decisoes so existe em PRODUCAO: o BehaviorCore nao
    # cria um sozinho para nao gravar em /mnt/ssd durante os testes.
    memoria_decisoes = MemoriaDeDecisoes()

    # Distancias do retrato contadas da LATARIA, com as mesmas medidas da
    # trava (auditoria C2-07).
    mundo = EstadoDoMundo(
        event_bus,
        corpo_frente_cm=corpo_frente_cm,
        corpo_tras_cm=corpo_tras_cm,
        corpo_meia_largura_cm=meia_largura_corpo_cm,
    )

    # Instinto da bussola: percebe que o rumo esta oscilando demais com o robo
    # parado e avisa que nao da para confiar nele. Assina a telemetria; nao
    # tem task propria. Ver motion_core/behavior/instintos.py para por que
    # isto nasceu (o fio do servo do radar solto, em 2026-07-29, com o
    # firmware reportando angulo com total confianca).
    conf_bussola = config.secao("behavior").get("bussola") or {}
    if conf_bussola:
        InstintoBussola(
            event_bus,
            dispersao_maxima_graus=conf_bussola["dispersao_maxima_graus"],
            amostras=conf_bussola["amostras"],
            tentativas_max=conf_bussola["tentativas_max"],
            intervalo_minimo_s=conf_bussola["intervalo_minimo_s"],
            pedir_calibracao=conf_bussola["pedir_calibracao"],
        )

    # Conselheiro de IA: desligado por padrao (ver config/orion.yaml,
    # behavior.conselho_ia) - a inferencia local saturava a CPU do Notebook
    # e derrubava o link TCP a cada consulta. `None` faz o maestro decidir
    # so pela regra, exatamente como antes de existir conselheiro.
    conf_conselho = config.secao("behavior").get("conselho_ia") or {}
    ponte_conselho = PonteConselhoIA(event_bus) if conf_conselho.get("habilitado") else None

    # O DIARIO CHEGA A IA ESTRATEGICA (2026-07-29).
    #
    # Ate hoje `observacoes` nao era passado aqui, entao `bloco_observacoes`
    # devolvia "o diario esta VAZIO: nao vi ninguem e nada aconteceu hoje" a
    # CADA ciclo - mesmo com o diario gravando e com o dono na frente da
    # camera. A IA decidia achando que a casa estava vazia e sem historia.
    #
    # `apenas_leitura=True` e obrigatorio: quem ESCREVE o diario e o Notebook,
    # onde a visao reconhece as pessoas. Como `vision.person_recognized` e
    # encaminhado para ca, um diario que assinasse eventos aqui gravaria cada
    # observacao DUAS vezes.
    #
    # Le direto da `MemoryAPI` local: o banco vive neste Raspberry (EDR-0019),
    # entao aqui nao ha rede no caminho - ao contrario do Notebook, que le pelo
    # MemoryClient atraves do link TCP.
    # Fica None aqui e e PREENCHIDO adiante, quando `memory_api` existir - o
    # closure abaixo ve a variavel, nao o valor do momento.
    diario_leitura = None

    #: O ultimo contexto que FOI registrado, para nao repetir o mesmo texto de
    #: 4 KB a cada 30 s. Ver a nota no fim de `_contexto_para_ia`.
    ultimo_contexto_logado = ""

    async def _contexto_para_ia() -> str:
        nonlocal ultimo_contexto_logado
        r = mundo.retrato()
        # Falha ao ler o diario NAO cancela a decisao: decidir com menos
        # contexto e ruim, decidir nunca e pior (Cap 6 s.8).
        observacoes = []
        if diario_leitura is not None:
            try:
                observacoes = await diario_leitura.observacoes_de_hoje()
            except Exception:
                logger.warning("falha ao ler o diario para o contexto da IA", exc_info=True)
        contexto = montar_contexto(
            retrato={
                "obstaculo_frente_cm": r.obstaculo_frente_cm,
                # `obstaculo_tras_cm` faltava aqui: o grounding pedia
                # `distancia_tras_cm`, nome que o RetratoDoMundo nunca teve, e
                # "espaco atras de mim" saia como "nao sei" SEMPRE.
                "obstaculo_tras_cm": r.obstaculo_tras_cm,
                "obstaculo_esquerda_cm": r.obstaculo_esquerda_cm,
                "obstaculo_direita_cm": r.obstaculo_direita_cm,
                "rumo_graus": r.rumo_graus,
                "posicao_x_m": r.posicao_x_m,
                "posicao_y_m": r.posicao_y_m,
                "objetos_vistos": r.objetos_vistos,
                "livre_por_direcao": r.livre_por_direcao,
                "desvio_frontal": r.desvio_frontal,
                "bateria_nivel": r.bateria_nivel,
                "estado_hardware": r.estado_hardware,
                "telemetria_viva": r.telemetria_viva,
                "pessoa_presente": r.pessoa_presente,
                "pessoa_nome": r.pessoa_nome,
                # Fecha o fio aberto em 2026-07-30: a navegacao deixou de
                # recusar avanco na duvida e passou a DECLARAR o risco. Sem
                # esta linha, a declaracao morreria no EstadoDoMundo e a IA
                # decidiria sem saber que o robo andou as cegas - ou seja, a
                # trava teria sido tirada sem nada no lugar.
                "risco_frontal_assumido": r.risco_frontal_assumido,
            },
            # (o log do que a IA de fato recebe sai logo abaixo, ver
            # `logger.info("contexto da IA...")` no fim desta funcao)
            # O limiar de verdade, lido da config - sem ele o texto usaria o
            # default do modulo e poderia divergir do que o robo de fato faz.
            limiar_obstaculo_cm=config_motion["min_front_distance_cm"],
            # Quem sao as pessoas da casa e o que elas sao PARA ELE.
            # Sem isto a IA decide sobre gente sem saber de quem se trata.
            familia=familia_conf,
            # O que ele viu hoje. Lista vazia tambem e informacao: vira "olhei
            # e nao vi nada", que e diferente de nao ter olhado.
            observacoes=observacoes,
        )
        # O QUE A IA DE FATO RECEBE (2026-07-30).
        #
        # Ate agora dava para ler a DECISAO dela ("IA decidiu 'explorar'") e o
        # MOTIVO que ela mesma escreve - mas nunca os FATOS que ela recebeu. E
        # o motivo e redacao dela, nao evidencia: ele diz o que ela acredita,
        # nao o que lhe foi dito.
        #
        # Sem esta linha, perguntas como "sera que ele viu obstaculo e ficou
        # com medo de andar?" nao tem resposta possivel - viram opiniao.
        # As quebras viram " | " para caber num registro so e dar para
        # acompanhar com `grep`.
        #
        # SO QUANDO MUDA (2026-08-04). Uma linha por ciclo custava caro demais:
        # o texto tem ~4 KB e o ciclo e de 30 s, entao em 8 horas foram 3.051
        # copias e 13 MB - o log virou o problema em vez da ferramenta, e para
        # ler qualquer outra coisa era preciso `grep -v "contexto da IA"`.
        #
        # Registrar so a MUDANCA nao perde a evidencia: o contexto vale ate ser
        # substituido, entao a ultima linha registrada e exatamente o que a IA
        # recebeu em todos os ciclos seguintes. Com o mundo parado sao poucas
        # linhas; quando algo muda de verdade, a mudanca aparece sozinha em vez
        # de ficar escondida entre mil repeticoes iguais.
        uma_linha = contexto.replace("\n", " | ")
        if uma_linha != ultimo_contexto_logado:
            ultimo_contexto_logado = uma_linha
            logger.info("contexto da IA: %s", uma_linha)
        else:
            logger.debug("contexto da IA: sem mudanca desde o registro anterior")
        return contexto

    # O pedido de conselho nasce no Pi e precisa CHEGAR ao Notebook (onde
    # a IA roda). Evento nao atravessa o link sozinho - alguem tem que
    # encaminhar com local=False (Cap 14 s.7); sem isto o maestro pergunta
    # no vazio e cai no timeout para sempre.
    async def _repassar_ao_notebook(evento) -> None:
        try:
            await comm.publish(evento.topico, dict(evento.dados), local=False)
        except Exception:
            logger.debug("falha ao repassar '%s' ao Notebook", evento.topico, exc_info=True)

    if ponte_conselho is not None:
        event_bus.subscribe(TOPICO_PEDIDO, _repassar_ao_notebook)

    # Alivio de carga: o pedido nasce no guardiao (aqui) e quem ATENDE e o
    # Notebook. Sem este repasse o evento morre no barramento do Pi - foi
    # exatamente o estado ate 2026-07-19, com o guardiao publicando no vazio
    # e a protecao contra travamento por memoria desligada sem ninguem
    # perceber (publicar evento nao falha).
    event_bus.subscribe(TOPICO_PEDIDO_ALIVIO, _repassar_ao_notebook)
    event_bus.subscribe(TOPICO_RECUPERADO, _repassar_ao_notebook)

    # Pedido de FOTO: nasce aqui (a Vigilia ao assumir um alerta, e a acao
    # `fotografar` do contrato, que a IA estrategica pode escolher) e quem tem
    # a camera e o Notebook. Ate 2026-07-29 este topico era publicado no
    # barramento do Pi e nao tinha assinante em NENHUMA das duas maquinas: o
    # robo decidia "registrar evidencia" e nada era registrado. A terceira
    # ocorrencia do mesmo padrao neste mesmo bloco - por isso o repasse mora
    # junto dos outros dois, onde a proxima pessoa vai olhar.
    event_bus.subscribe("sentinela.capturar_foto", _repassar_ao_notebook)

    # Pan/tilt vindo dos COMPORTAMENTOS (Repouso nivela a cabeca em 0,0;
    # ObservarAmbiente varre -60 -> +60 -> 0). Eles publicam `motion.pan_tilt`
    # no barramento do Pi e, ate 2026-07-28, NINGUEM assinava esse topico
    # aqui - o mesmo "publicar no vazio" do guardiao de RAM logo acima, e
    # publicar evento nao falha, entao nada denunciava.
    #
    # O sintoma foi caro: com a camera apontada para uma parede o robo nao
    # tinha COMO se desvirar. O unico emissor real de SET_PAN_TILT era o
    # Vision Core do Notebook, que so manda quando JA esta rastreando alguem
    # - e ele nao via ninguem justamente por estar olhando para a parede.
    # A varredura que existia para resolver isso publicava no vazio.
    #
    # O log ainda mentia junto: Repouso escreve "cabeca nivelada" com
    # logger.info ao lado de um publish que ninguem consumia, afirmando uma
    # acao que nunca acontecia.
    #
    # Metade deste fio ja tinha sido ligada em 2026-07-25, no lado da visao
    # (src/orion/vision/modulo.py `_mover_servos`); esta e a outra metade.
    # Limitador de taxa igual ao de la e pelo mesmo motivo medido: um comando
    # por quadro afoga a serial e atrasa os heartbeats, que e justamente como
    # o sistema detecta falha.
    #
    # SO POR TEMPO, NUNCA POR VALOR IGUAL (2026-07-28, bug real):
    # a primeira versao disto tambem descartava o comando quando pan/tilt nao
    # tinham mudado mais que 1 grau em relacao ao ULTIMO QUE O PI MANDOU -
    # copiado do lado da visao, onde faz sentido porque la o mesmo emissor
    # manda tudo. Aqui NAO faz: quem move o servo na maior parte do tempo e o
    # Vision Core, DIRETO do Notebook, sem passar por este barramento. Entao
    # este cache mentia sobre a posicao real da cabeca. O sintoma foi exato:
    # a visao levava a camera para um lado atras de uma deteccao, o alvo
    # sumia, o Repouso pedia (0,0) para nivelar e este `return` engolia o
    # comando por achar que a cabeca "ja estava" em (0,0) - a camera ficava
    # presa apontada para onde a visao a deixou, sem nunca mais voltar.
    #
    # Cache de estado de hardware que so ve METADE dos comandos e pior que
    # nenhum cache. O limitador por TEMPO continua, e sozinho ja basta: no
    # barramento do Pi so entram comandos de COMPORTAMENTO (Repouso ao
    # assumir, Ronda varrendo), que sao raros - nao existe aqui a enxurrada
    # de 15 quadros/s que motivou a deduplicacao original.
    ultimo_pan_tilt = {"ts": 0.0, "operador_ts": 0.0, "centrada": True}

    #: Recentragem automatica da cabeca (2026-08-04), pedida pelo dono.
    #:
    #: 3 min de OCIOSIDADE, nao de relogio - ver `_recentrar_cabeca_ociosa`.
    #: A checagem e grossa (15 s) porque o alvo e "esqueceram a cabeca virada",
    #: e nao um instante exato: acordar de 15 em 15 s para comparar dois floats
    #: nao custa nada e evita um timer que precisaria ser rearmado a cada
    #: comando.
    RECENTRAGEM_OCIOSIDADE_S = 180.0
    RECENTRAGEM_CHECAGEM_S = 15.0
    #: Assinatura da recentragem, para o handler a deixar passar mesmo em
    #: MANUAL. Nome proprio e nao `ORIGEM_OPERADOR` de mentira: quem mandou foi
    #: o robo, e o log tem que poder dizer isso.
    ORIGEM_RECENTRAGEM = "recentragem_ociosidade"

    #: Preenchido mais abaixo (o BehaviorCore nasce depois deste handler).
    #: Declarado aqui para o closure ter o nome ligado se um evento de pan/tilt
    #: chegar entre o subscribe e a construcao do maestro - sem isto seria um
    #: NameError numa janela de milissegundos, que e o tipo de falha que so
    #: aparece na madrugada de quinta.
    maestro = None

    #: Quem mexeu no joystick da cabeca fica DONO dela por este tempo, e os
    #: comportamentos esperam a vez (2026-08-03).
    #:
    #: Sem isto o joystick "nao funcionava": `observar_ambiente` varre a cabeca
    #: de -60 a +60 e o GOAP escolhe esse objetivo a cada ~30 s, entao o robo
    #: tomava a cabeca de volta poucos segundos depois de o dono a virar - de
    #: fora de casa isso e indistinguivel de defeito.
    #:
    #: A janela EXPIRA sozinha de proposito. Um "modo manual da cabeca" com
    #: botao para ligar e desligar teria o defeito oposto: o robo pararia de
    #: olhar em volta para sempre porque alguem esqueceu de devolver o
    #: controle - e isso ninguem percebe ate precisar.
    JANELA_DO_OPERADOR_S = 10.0

    #: Limitador de taxa. Protege a serial de uma enxurrada de comandos (que
    #: atrasa os heartbeats, que e como o sistema detecta falha). Menor que o
    #: intervalo do joystick (120 ms) de proposito: em 150 ms ele descartava
    #: quase um comando em cada dois, e como o comando e ANGULO ABSOLUTO,
    #: perder o ultimo e parar a cabeca onde o penultimo mandou - com a tela
    #: mostrando outra coisa.
    INTERVALO_MINIMO_S = 0.1

    async def _mover_servos_do_maestro(evento: Evento) -> None:
        pan = float(evento.dados.get("pan", 0.0))
        tilt = float(evento.dados.get("tilt", 0.0))
        agora = time.monotonic()
        do_operador = evento.dados.get("origem") == ORIGEM_OPERADOR

        if do_operador:
            ultimo_pan_tilt["operador_ts"] = agora
        elif evento.dados.get("origem") == ORIGEM_RECENTRAGEM:
            # A recentragem por ociosidade PASSA por cima de tudo, e e a unica
            # que passa. Ver `_recentrar_cabeca_ociosa`: ela so dispara depois
            # de 3 min sem NENHUM comando, entao nunca disputa a cabeca com
            # ninguem - quando ela fala, nao ha mais ninguem falando.
            pass
        elif maestro is not None and maestro.modo_controle == MODO_MANUAL:
            # EM MANUAL A CABECA E DO OPERADOR, E NAO POR 10 SEGUNDOS
            # (2026-08-04).
            #
            # A janela de 10 s abaixo foi feita para o modo AUTONOMO, onde ela
            # esta certa: o robo precisa voltar a olhar em volta sozinho, e um
            # controle manual que nunca expira e um robo que para de vigiar
            # porque alguem esqueceu de devolver a cabeca.
            #
            # Em MANUAL o raciocinio se inverte. O dono declarou que esta
            # dirigindo; nao ha nada para o robo retomar. O que acontecia sem
            # esta linha: o dono virava a cabeca, e 10 s depois o `Repouso`
            # (que publica pan/tilt 0,0 ao assumir, para nivelar depois de um
            # reset da placa) a puxava de volta ao centro. Da tela isso e
            # exatamente *"eu aperto e volta"* - indistinguivel de defeito.
            logger.debug(
                "pan/tilt do maestro ignorado: modo MANUAL, a cabeca e do operador"
            )
            return
        elif agora - ultimo_pan_tilt["operador_ts"] < JANELA_DO_OPERADOR_S:
            # A cabeca esta na mao do dono. Em debug e nao em warning: isto e
            # o funcionamento correto, nao uma falha - e um warning a cada
            # varredura do maestro afogaria o log de verdade.
            logger.debug(
                "pan/tilt do maestro ignorado: cabeca com o operador (pan=%.1f tilt=%.1f)",
                pan, tilt,
            )
            return

        if agora - ultimo_pan_tilt["ts"] < INTERVALO_MINIMO_S:
            return
        ultimo_pan_tilt["ts"] = agora
        # Sai do centro? entao volta a haver o que recentrar. Sem esta linha a
        # recentragem aconteceria UMA vez e nunca mais - o defeito silencioso
        # classico: funciona no teste da bancada e morre no segundo dia.
        ultimo_pan_tilt["centrada"] = pan == 0 and tilt == 0
        try:
            await comm.send(
                "hardware_core",
                {"comando": "SET_PAN_TILT", "pan_graus": pan, "tilt_graus": tilt},
            )
            await _cabeca_movida(pan, tilt, evento.dados.get("origem"))
        except ErroComunicacao:
            # Mega ausente nao derruba o maestro (Cap 6 s.8), mas isto vai em
            # WARNING e NAO em debug de proposito: foi exatamente um
            # `logger.debug` que escondeu esta mesma classe de falha no lado
            # da visao - o servo nao se mexia e o log ficava limpo.
            logger.warning("nao consegui mover o pan/tilt (pan=%.1f tilt=%.1f)", pan, tilt)

    event_bus.subscribe("motion.pan_tilt", _mover_servos_do_maestro)

    async def _cabeca_movida(pan: float, tilt: float, origem) -> None:
        """A cabeca FOI mandada para (pan, tilt) - o fato, nao o pedido.

        `motion.pan_tilt` e PEDIDO: este Pi descarta parte deles (MANUAL,
        janela do operador, limitador de taxa) e, ate 12/11, o FOLLOW, o
        retrato e o Notebook atualizavam a posicao mesmo assim (auditoria
        C6-02). E o comando da visao do Notebook nem passava por aqui (C6-01).
        Agora so o que saiu para o ESP32 vira `motion.cabeca_posicao`, e so
        isso vai ao Notebook - que ignora o eco da propria visao.
        """
        dados = {"pan": pan, "tilt": tilt, "origem": origem}
        await event_bus.publish("motion.cabeca_posicao", dados)
        if origem != "visao":
            try:
                await comm.publish("motion.pan_tilt", dict(dados), local=False)
            except Exception:
                logger.debug("falha ao repassar a cabeca ao Notebook", exc_info=True)

    async def _repasse_ao_corpo(mensagem) -> None:
        """Comando que o Pi REPASSOU ao corpo (o Notebook move a cabeca direto)."""
        payload = mensagem.payload or {}
        if payload.get("comando") != "SET_PAN_TILT":
            return
        try:
            pan = float(payload.get("pan_graus", 0.0))
            tilt = float(payload.get("tilt_graus", 0.0))
        except (TypeError, ValueError):
            return
        await event_bus.publish(
            "motion.cabeca_posicao", {"pan": pan, "tilt": tilt, "origem": "visao"}
        )

    comm.definir_gancho_repasse("hardware_core", _repasse_ao_corpo)

    async def _recentrar_cabeca_ociosa() -> None:
        """Depois de 3 min sem ninguem mexer, a cabeca volta ao centro.

        PEDIDO DO DONO (2026-08-04): *"poe um alerta pra cabeca se centralizar
        automaticamente a cada 3 minutos"*.

        POR OCIOSIDADE, E NAO PELO RELOGIO. Recentrar a cada 3 min de relogio
        arrancaria a cabeca do dedo do dono no meio do uso - e o mesmo defeito
        que o `Repouso` causava aos 10 s, so que mais raro e por isso MAIS
        dificil de entender quando acontecesse. Contando desde o ULTIMO
        comando, ela so age quando ninguem mais esta agindo.

        POR QUE PRECISOU EXISTIR: hoje o modo MANUAL passou a segurar a cabeca
        para o operador indefinidamente (ver `_mover_servos_do_maestro`), o que
        conserta o *"eu aperto e volta"* mas deixa a cabeca virada para um canto
        para sempre se o dono sair sem centralizar. Esta e a rede embaixo
        daquilo: quem esquecer, o robo desfaz sozinho.

        A camera do pan/tilt e como o dono ve a casa de fora. Uma cabeca
        apontada para a parede desde ontem e uma camera que nao serve para
        nada, sem nada denunciando.
        """
        while True:
            await asyncio.sleep(RECENTRAGEM_CHECAGEM_S)
            ocioso = time.monotonic() - max(
                ultimo_pan_tilt["ts"], ultimo_pan_tilt["operador_ts"]
            )
            if ocioso < RECENTRAGEM_OCIOSIDADE_S:
                continue
            if ultimo_pan_tilt.get("centrada"):
                # Ja esta no centro: nao ha o que recentrar, e repetir gastaria
                # serial e log a cada checagem, para sempre.
                continue
            logger.info(
                "cabeca ociosa ha %.0f s - voltando ao centro sozinha", ocioso
            )
            await event_bus.publish(
                "motion.pan_tilt", {"pan": 0, "tilt": 0, "origem": ORIGEM_RECENTRAGEM}
            )
            ultimo_pan_tilt["centrada"] = True

    tarefa_recentragem = asyncio.create_task(_recentrar_cabeca_ociosa())

    # ...e o Notebook precisa SABER que a cabeca se mexeu (2026-07-29).
    #
    # A visao guarda a posicao dos servos em software (CalculadoraPanTilt) e
    # calcula a proxima correcao a partir dela. Quando a ronda daqui joga a
    # cabeca para -60 e a visao ainda acha que esta em +20, a correcao seguinte
    # sai de uma base que nao existe e a cabeca pula quase 90 graus - o
    # "pan/tilt fica maluco depois de horas funcionando bem".
    #
    # O repasse e SO neste sentido. O Notebook nao encaminha `motion.pan_tilt`
    # para ca, e nao pode passar a encaminhar: a visao ja comanda os servos
    # pelo `comm.send` direto, e o evento faria o mesmo movimento uma segunda
    # vez por este handler.
    # (2026-11-12, C6-02) O repasse ao Notebook saiu daqui: ele agora e feito
    # em `_cabeca_movida`, SO para o que de fato foi mandado ao corpo. Antes
    # ia todo pedido, inclusive os descartados, e a visao ressincronizava
    # para uma posicao onde a cabeca nunca foi.

    # O MODO tambem (2026-11-08). A visao do Notebook passou a mover a cabeca
    # sozinha (mirar o rosto, procurar quando nao ve) e precisa saber quando o
    # dono pegou o volante: em MANUAL a cabeca e do operador. O evento ja
    # existia - a trava do LiDAR o escuta aqui -, so nao atravessava o enlace.
    event_bus.subscribe("behavior.modo_controle", _repassar_ao_notebook)

    maestro = BehaviorCore(
        event_bus,
        ponte_conselho=ponte_conselho,
        montar_contexto=_contexto_para_ia,
        intervalo_consulta_ia_s=conf_conselho.get("intervalo_consulta_s", 120),
        memoria_decisoes=memoria_decisoes,
    )
    maestro.registrar(Repouso(event_bus))
    # Discricionario: e entre este e o repouso que a IA opina (EDR-0020).
    ronda = Ronda(event_bus)
    maestro.registrar(ronda, discricionario=True)
    maestro.registrar(Atender(event_bus))
    # Segura a cabeça enquanto a visão rastreia alguém, para o Repouso não
    # recentrar o pan/tilt por baixo do rastreamento (2026-07-28).
    maestro.registrar(AcompanharPessoa(event_bus))
    maestro.registrar(Vigilia(event_bus, config.secao("behavior")["vigilia"]["duracao_alerta_s"]))
    maestro.registrar(AtenderGesto(event_bus))
    maestro.registrar(ControleManual(event_bus))
    maestro.registrar(VigilanciaObstaculo(event_bus))

    conf_ia_estrategica = config.secao("behavior")["ia_estrategica"]
    tarefa_ia_estrategica = None
    if conf_ia_estrategica.get("habilitado"):
        ia_estrategica = IaEstrategica(
            event_bus,
            comm,
            intervalo_s=conf_ia_estrategica["intervalo_s"],
            # os sensores respondem em poucos segundos; a 1a consulta espera
            espera_inicial_s=float(conf_ia_estrategica.get("espera_inicial_s", 20.0)),
            # O quadro do mundo (EstadoDoMundo + grounding) vai no prompt de
            # quem DECIDE, e nao so no do conselheiro desligado - ate
            # 2026-07-29 esta IA recebia uma linha de contexto e decidia
            # praticamente cega. Ver o comentario em IaEstrategica.__init__.
            montar_contexto=_contexto_para_ia,
            # O QUE LIGA O GOAP (2026-08-03). Com o retrato em maos a IA passa
            # a escolher um OBJETIVO e o planejador monta a sequencia ate ele -
            # que e o que `goap.py` promete desde 02/08 sem nunca ter sido
            # chamado por ninguem fora dos testes.
            #
            # E `mundo.retrato` (o metodo, sem parenteses): o estado tem que
            # ser lido NA HORA de planejar. Passar o retrato ja montado aqui
            # congelaria o mundo no instante do boot - exatamente o "dado velho
            # nao e dado" que o EstadoDoMundo existe para impedir.
            #
            # DESLIGADO POR PADRAO (2026-11-01, dono: "muito chefe para pouco
            # indio"). Com o GOAP a IA escolhia um objetivo e ele traduzia em
            # acao por conta propria - e girou para a parede seis vezes. Agora
            # a IA escolhe a acao direto, com o retrato das 8 direcoes na mao.
            # Volta com `behavior.ia_estrategica.goap: true`.
            retrato=mundo.retrato if conf_ia_estrategica.get("goap", False) else None,
            # A IA ESCOLHE INTENCAO, NAO MANOBRA (2026-11-01, dono: "a ia nao
            # precisa fazer manobras, isso sai caro" / "a ia so decide onde ir
            # e o corpo usa os motores e sensores para ir"). Na 1a volta do
            # autonomo com o freio novo ela escolheu desviar_esquerda_mm: girou
            # 90 graus para uma parede a 47 cm e a trava recusou. Quem desvia e
            # o EXPLORE (desvio_local.py). Sem a chave, o catalogo inteiro.
            acoes_validas=tuple(
                conf_ia_estrategica.get("acoes_permitidas") or contrato.NOMES
            ),
        )
        ia_estrategica.prioridade = conf_ia_estrategica["prioridade"]
        # Guarda-rail (achado da vistoria de 2026-07-24): nenhum
        # comportamento nao-reativo pode ultrapassar a prioridade da
        # seguranca tatica - antes um valor mal digitado no orion.yaml
        # nao tinha nenhuma barreira de codigo contra isso.
        assert ia_estrategica.prioridade < VigilanciaObstaculo.prioridade, (
            "behavior.ia_estrategica.prioridade nao pode ser >= "
            "VigilanciaObstaculo.prioridade (seguranca nunca e arbitrada)"
        )
        maestro.registrar(ia_estrategica)
        tarefa_ia_estrategica = ia_estrategica.iniciar()

    # O Mentor foi FUNDIDO na IaEstrategica em 2026-07-28 (EDR-0026): era a
    # mesma IA, no mesmo provedor, perguntada de novo mais devagar - e o
    # conselho dele nunca virava acao. Some junto a `tarefa_mentor`, que era
    # criada aqui e nunca cancelada no desligamento (pendencia de 26/07).

    # O CATALOGO PROMETE, O MAESTRO ENTREGA - conferido aqui (2026-07-30).
    #
    # Este e o unico lugar que conhece as duas listas: o que a IA pode
    # escolher (`contrato.py`) e o que de fato foi registrado. A `Ronda`
    # passou dias registrada e inalcancavel sem um unico erro no log, porque
    # ninguem cruzava as duas pontas. Falhar aqui e no BOOT, alto e claro, e
    # melhor que descobrir em campo que a IA escolheu algo que nao existe.
    faltando = contrato.COMPORTAMENTOS_DELEGADOS - set(maestro.nomes_registrados)
    if faltando:
        raise RuntimeError(
            f"o contrato delega para comportamento(s) que ninguem registrou: "
            f"{sorted(faltando)} - a IA poderia escolher uma acao sem executor"
        )

    tarefa_maestro = asyncio.create_task(maestro.executar())
    # 0 POR PADRAO (2026-11-01, mesmo motivo do GOAP): a ronda a cada 90 s era
    # mais um chefe mexendo a cabeca por conta propria. Ela continua no
    # catalogo da IA - quem decide fazer ronda e a IA.
    intervalo_ronda_s = config.secao("behavior").get("ronda_automatica_intervalo_s", 0.0)

    async def _pedir_ronda_periodica() -> None:
        """Faz a camera principal observar o ambiente quando o robo esta livre.

        A Ronda continua subordinada ao maestro: pessoa, voz, seguranca e o
        operador a preemptam; no modo manual ela nem chega a ser pedida.
        """
        while True:
            await asyncio.sleep(intervalo_ronda_s)
            if maestro.modo_controle == MODO_AUTONOMO:
                await event_bus.publish(
                    Ronda.TOPICO_PEDIDO, {"origem": "varredura_visual_periodica"}
                )

    tarefa_ronda = None
    if intervalo_ronda_s > 0:
        tarefa_ronda = asyncio.create_task(_pedir_ronda_periodica())
        logger.info("Ronda visual automatica ativa a cada %.0f s", intervalo_ronda_s)
    logger.info(
        "Behavior Core (maestro) ativo - comportamentos: %s",
        ", ".join(maestro.nomes_registrados),
    )

    # 4. Memoria (Fase 3) - opcional, so se o SSD de producao existir.
    conf_db = config.secao("database")
    memory_api, db, vault = _abrir_memoria(config, event_bus, logger)
    if memory_api is not None:
        # Agora sim: o banco existe, e o contexto da IA passa a ter o diario.
        diario_leitura = DiarioObservacoes(event_bus, memory_api, apenas_leitura=True)
        # O aprendizado por experiencia roda SO no Pi: os desfechos
        # navigation.* nascem neste barramento, e e daqui que o produtor
        # transforma o que aconteceu em experiencias buscaveis pelo portao.
        registrador_experiencias = RegistradorExperiencias(event_bus, memory_api)
        # Ciclo fechado (Camada 6): a navegacao nasceu sem memoria (passo 3,
        # antes do SSD). Com a MemoryAPI no ar, liga o degrau que conta
        # repeticoes de falha e evita que o robo repita um erro de frente.
        navigation.registrar_consulta_repeticoes(
            lambda situacao, janela_s: memory_api.contar_repeticoes(
                situacao, "falhou", janela_s
            )
        )
        # O seguir aprende: as ultimas tentativas vao junto quando ele pergunta
        # a IA o que fazer (08/11).
        navigation.registrar_consulta_experiencias(memory_api.experiencias_recentes)
    tarefa_manutencao = None
    tarefa_manutencao_task = None
    if memory_api is not None:
        # Sem esta ponte o banco ate abre, mas os comandos memory.* vindos
        # do Notebook via comm.request nunca chegam na MemoryAPI (fio solto
        # achado na primeira integracao completa, 2026-07-19).
        PonteMemoria(memory_api, comm, vault).registrar(event_bus)
        logger.info("Ponte de memoria registrada - comandos memory.* ativos via TCP")

        tarefa_manutencao = _iniciar_manutencao(db, event_bus, comm, conf_db, logger)
        tarefa_manutencao_task = asyncio.create_task(tarefa_manutencao.iniciar())
        logger.info(
            "Backup diario agendado para %02d:00 (replica para o Notebook: %s)",
            conf_db["backup_hour"],
            "ativada" if conf_db.get("replica_to_notebook") else "desativada",
        )

    # 5. Interface web (Cap 13 s.4).
    conf_web = config.secao("display")["web"]

    # O BOTAO DE DESLIGAR AS DUAS MAQUINAS (2026-08-04).
    #
    # O `desligamento.py` e os testes dele entraram no repo em 03/08 e ate hoje
    # NINGUEM os chamava - faltava esta construcao, a rota e o botao. E o
    # segundo caso de codigo orfao no mesmo mes (o outro foi o GOAP): escrever,
    # testar e commitar nao liga nada sozinho.
    #
    # Sai None se o bloco `communication.notebook` nao existir, e ai o botao
    # aparece recusando com "desligamento nao configurado" em vez de a pagina
    # quebrar - ou, pior, de o botao existir e nao fazer nada.
    conf_notebook = config.secao("communication").get("notebook") or {}
    desligador = None
    if conf_notebook.get("host"):
        desligador = Desligador(
            host_notebook=conf_notebook["host"],
            usuario_notebook=conf_notebook.get("usuario", "jproma23"),
        )
        logger.info(
            "Desligamento pela webui ativo - o Notebook sera alcancado em %s@%s",
            conf_notebook.get("usuario", "jproma23"),
            conf_notebook["host"],
        )
    else:
        logger.warning(
            "sem communication.notebook no orion.yaml - o botao de desligar "
            "ficara recusando: nao ha como alcancar a outra maquina"
        )

    webui = WebUIServer(
        event_bus,
        port=conf_web["port"],
        memory_api=memory_api,
        config=config,
        # O botao AUTONOMO/MANUAL do dashboard precisa falar com quem arbitra.
        # A webui nao guarda copia do modo: le do maestro a cada consulta, para
        # a tela nunca discordar de quem de fato esta no volante.
        maestro=maestro,
        # O chat por texto da aba Conversa e o UNICO consumidor: a IA roda no
        # Notebook, entao a caixa de texto precisa deste link para chegar la.
        comm=comm,
        # Quem executa o desligamento das duas maquinas (ver acima).
        desligador=desligador,
        # O retrato do mundo vai para a tela (2026-09-19). Sem isto, a unica
        # forma de saber se o robo enxerga a frente era ler o log da IA
        # estrategica - que esta desligada desde 07/08. O dado existia e era
        # invisivel.
        mundo=mundo,
    )
    await webui.iniciar()

    # BANCO RECRIADO TEM QUE APARECER (auditoria 12/11). O banco abre no
    # inicio do boot (`_abrir_memoria`, db.iniciar sincrono) e o
    # `iniciar_banco()` que publicaria `database.rebuilt` nunca era chamado em
    # producao - e ainda que fosse, a webui so nasce aqui. Banco recriado do
    # zero = memoria do robo perdida, e so existia uma linha no log.
    if db is not None and getattr(db, "foi_reconstruido", False):
        await event_bus.publish(
            "database.rebuilt", {"caminho": str(db.caminho_db)}, prioridade=Prioridade.ALTA
        )

    registry.atualizar_estado(NOME_MODULO, EstadoModulo.RUNNING)
    await event_bus.publish(
        "system.ready",
        {"robot_name": secao_sistema["robot_name"], "modo": "motion_core"},
        prioridade=Prioridade.CRITICA,
    )
    await event_bus.aguardar_fila_vazia()
    logger.info(
        "Motion Core pronto - interface web em http://0.0.0.0:%d (Ctrl+C para sair)",
        conf_web["port"],
    )

    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sinal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sinal, parar.set)
    await parar.wait()

    logger.info("Motion Core: iniciando desligamento seguro")
    heartbeat.parar()
    tarefa_heartbeat.cancel()
    tarefa_watchdog_trava.cancel()
    tarefa_recentragem.cancel()
    if tarefa_ronda is not None:
        tarefa_ronda.cancel()
    maestro.parar()
    tarefa_maestro.cancel()
    if tarefa_ia_estrategica is not None:
        ia_estrategica.parar()
        tarefa_ia_estrategica.cancel()
    if vision_engine is not None:
        vision_engine.parar()
    if tarefa_manutencao is not None:
        tarefa_manutencao.parar()
        tarefa_manutencao_task.cancel()
    if lidar_manager is not None:
        await lidar_manager.parar()
    if gerente_nav2 is not None:
        # So para o LACO de ociosidade. O container fica como estiver: se o
        # Motion Core esta caindo para um restart, derrubar o Nav2 junto so
        # faria o proximo goal pagar 15 s a toa.
        gerente_nav2.parar()
        if tarefa_gerente_nav2 is not None:
            tarefa_gerente_nav2.cancel()
    await webui.encerrar()
    await comm.encerrar()
    servidor_tcp.close()
    await servidor_tcp.wait_closed()
    event_bus.parar()
    tarefa_bus.cancel()
    tarefas_para_aguardar = [tarefa_heartbeat, tarefa_maestro, tarefa_bus]
    if tarefa_ia_estrategica is not None:
        tarefas_para_aguardar.append(tarefa_ia_estrategica)
    if tarefa_manutencao_task is not None:
        tarefas_para_aguardar.append(tarefa_manutencao_task)
    for tarefa in tarefas_para_aguardar:
        try:
            await tarefa
        except asyncio.CancelledError:
            pass
    registry.atualizar_estado(NOME_MODULO, EstadoModulo.STOPPED)
    logger.info("Motion Core encerrado")


if __name__ == "__main__":
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        pass
