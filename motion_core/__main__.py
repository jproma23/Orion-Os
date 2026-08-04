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
import signal
import time
from pathlib import Path

from motion_core.behavior import contrato
from motion_core.behavior.behavior_core import BehaviorCore
from motion_core.behavior.comportamentos import (
    AcompanharPessoa,
    Atender,
    AtenderGesto,
    ControleManual,
    IaEstrategica,
    Repouso,
    Ronda,
    Vigilia,
    VigilanciaObstaculo,
)
from motion_core.behavior.estado_do_mundo import EstadoDoMundo
from motion_core.behavior.instintos import InstintoBussola
from motion_core.behavior.guardiao_ram import GuardiaoRamNotebook
from motion_core.behavior.ponte_conselho import PonteConselhoIA
from orion.mission.alivio_carga import TOPICO_PEDIDO_ALIVIO, TOPICO_RECUPERADO
from orion.mission.conselho_protocolo import TOPICO_PEDIDO
from orion.mission.diario import DiarioObservacoes
from orion.mission.grounding import montar_contexto
from motion_core.memory.api import MemoryAPI
from motion_core.memory.bridge import PonteMemoria
from motion_core.memory.database import DatabaseManager
from motion_core.memory.manutencao import TarefaManutencao
from motion_core.memory.replica import replicar_backup
from motion_core.memory.vault import VaultConhecimento
from motion_core.navigation.fusao_sensores import FusaoSensores
from motion_core.navigation.navigation_core import NavigationCore
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

VERSAO_MOTION_CORE = "0.1.0"
NOME_MODULO = "motion_core"

#: "auto" em communication.hardware_core.port (Cap 17) = tentar estas portas em
#: ordem ate uma responder ao WHO_ARE_YOU. O CH340 do Mega re-enumera entre
#: ttyUSB0/ttyUSB1 quando o USB e replugado ou ha varias reconexoes (visto
#: 2026-07-19) - varrer a lista evita "Arduino sumiu" por causa disso.
PORTAS_SERIAIS_CANDIDATAS = ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0")


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
    except (ErroVersaoIncompativel, ErroComunicacao):
        # deixa o link registrado (comm.encerrar fecha o transporte no fim);
        # uma proxima candidata que responda sobrescreve "hardware_core".
        return False

    logger.info("Hardware Core (ESP32) conectado e confirmado em %s", porta)
    return True


async def _conectar_hardware_core(
    comm: ComunicacaoService,
    config_hardware_core: dict,
    event_bus: EventBus,
    logger: logging.Logger,
) -> bool:
    """Abre o link serial com o Hardware Core e confirma via WHO_ARE_YOU.
    Retorna False (tolerado, Cap 6 s.8) se nenhuma porta responder - nunca
    levanta excecao."""
    porta = config_hardware_core["port"]
    candidatas = PORTAS_SERIAIS_CANDIDATAS if porta == "auto" else (porta,)

    for candidata in candidatas:
        if await _tentar_porta(comm, candidata, config_hardware_core["baud_rate"], event_bus, logger):
            return True

    logger.warning("Hardware Core nao encontrado em nenhuma porta: %s", ", ".join(candidatas))
    await event_bus.publish(
        "diagnostic.error",
        {"modulo": "hardware_core", "motivo": "serial_indisponivel", "portas": list(candidatas)},
        prioridade=Prioridade.ALTA,
    )
    return False


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
    backup para o Notebook (Cap 15 s.6) assim que ele terminar. A replica
    tolera o Notebook estar desconectado no momento - tenta de novo no
    proximo backup (Cap 6 s.8)."""
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

        async def _ao_backup_completar(evento: Evento) -> None:
            caminho = Path(evento.dados["arquivo"])
            try:
                await replicar_backup(caminho, comm, destino="mission_core")
                logger.info("Backup replicado para o Notebook: %s", caminho.name)
            except ErroComunicacao as erro:
                logger.warning(
                    "Notebook indisponivel para replica do backup (%s) - tenta de novo "
                    "no proximo backup diario",
                    erro,
                )

        event_bus.subscribe("database.backup_completed", _ao_backup_completar)

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
    hardware_core_conectado = await _conectar_hardware_core(comm, conf_comm["hardware_core"], event_bus, logger)
    if hardware_core_conectado:
        # Sem tolerancia extra DE PROPOSITO: este e o caminho da seguranca
        # reativa - descobrir tarde que o Mega sumiu e pior que um falso
        # alarme (Cap 18).
        heartbeat.monitorar(
            "hardware_core",
            heartbeats_perdidos_limite=limites_por_link.get("hardware_core"),
        )

    tarefa_heartbeat = asyncio.create_task(heartbeat.iniciar())

    # 3. Navegacao + Fusao de Sensores (Cap 12) - registradas mesmo sem
    # Arduino conectado; comandos so falhariam com ErroComunicacao na hora
    # de enviar, tratado normalmente pelo _ao_receber_comando existente.
    config_motion = config.secao("motion")
    config_navigation = config.secao("navigation")
    NavigationCore(event_bus, comm, config_motion, config_navigation)
    FusaoSensores(event_bus, config_motion)

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

    mundo = EstadoDoMundo(event_bus)

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
                "inclinacao_graus": r.inclinacao_graus,
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
        # Sem esta linha, perguntas como "sera que a inclinacao esta alta e ela
        # esta com medo de andar?" nao tem resposta possivel - viram opiniao.
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
    ultimo_pan_tilt = {"ts": 0.0, "operador_ts": 0.0}

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
        try:
            await comm.send(
                "hardware_core",
                {"comando": "SET_PAN_TILT", "pan_graus": pan, "tilt_graus": tilt},
            )
        except ErroComunicacao:
            # Mega ausente nao derruba o maestro (Cap 6 s.8), mas isto vai em
            # WARNING e NAO em debug de proposito: foi exatamente um
            # `logger.debug` que escondeu esta mesma classe de falha no lado
            # da visao - o servo nao se mexia e o log ficava limpo.
            logger.warning("nao consegui mover o pan/tilt (pan=%.1f tilt=%.1f)", pan, tilt)

    event_bus.subscribe("motion.pan_tilt", _mover_servos_do_maestro)

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
    event_bus.subscribe("motion.pan_tilt", _repassar_ao_notebook)

    maestro = BehaviorCore(
        event_bus,
        ponte_conselho=ponte_conselho,
        montar_contexto=_contexto_para_ia,
        intervalo_consulta_ia_s=conf_conselho.get("intervalo_consulta_s", 120),
    )
    maestro.registrar(Repouso(event_bus))
    # Discricionario: e entre este e o repouso que a IA opina (EDR-0020).
    maestro.registrar(Ronda(event_bus), discricionario=True)
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
            retrato=mundo.retrato,
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
    )
    await webui.iniciar()

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
    maestro.parar()
    tarefa_maestro.cancel()
    if tarefa_ia_estrategica is not None:
        ia_estrategica.parar()
        tarefa_ia_estrategica.cancel()
    if tarefa_manutencao is not None:
        tarefa_manutencao.parar()
        tarefa_manutencao_task.cancel()
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
