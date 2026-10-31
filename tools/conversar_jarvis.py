"""Conversa de voz ao vivo com o Jarvis no Notebook (Caps 7, 9 e 13).

Junta num processo só, no mesmo Event Bus:
  - AvatarServer (Cap 13) na porta 8090 - o kiosk do Firefox na TV mostra
    o avatar reagindo aos estados de voz de verdade;
  - VoiceCore (Cap 9) - escuta o microfone, espera o nome dele (voice.wake_word),
    transcreve o comando com o Whisper;
  - AiManager (Cap 7) - responde com a IA remota (OpenRouter);
  - Sintetizador (Cap 9) - fala a resposta pela TV (HDMI).

E o substituto ao vivo do tools/preview_avatar.py (que so simula eventos):
pare o servico do preview antes de rodar este script, senao a porta 8090
ja estara ocupada:

    systemctl --user stop orion-avatar.service
    cd ~/orion-os && .venv/bin/python tools/conversar_jarvis.py

Fluxo de uso: espere o "pronto" no log (a primeira carga do Whisper pode
demorar), diga "Jarvis" perto do microfone, aguarde o avatar mudar para
WAKE_DETECTED e fale o comando/pergunta. Ctrl+C para sair.

Com o Motion Core de pe no Raspberry (2026-07-19), este processo agora e o
Mission Core completo do criterio da Fase 6: conecta via TCP no Raspberry
e os comandos de voz passam primeiro pelo Mission Planner (Cap 7 s.4) -
comandos predefinidos ("acende a lanterna" -> LIGHT_ON no Mega pela cadeia
TCP+serial; "que horas sao" -> resposta direta) e so a conversa livre vai
para a IA (gemma3). Sem o Raspberry alcancavel, degrada graciosamente
(Cap 6 s.8): a conversa continua, so os comandos de hardware e a memoria
persistente ficam indisponiveis.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

_RAIZ_PROJETO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ_PROJETO / "src"))
sys.path.insert(0, str(_RAIZ_PROJETO))  # expoe motion_core/ (replica do backup)

from motion_core.memory.replica import ReceptorReplica  # noqa: E402
from orion.communication.heartbeat import MonitorHeartbeat  # noqa: E402
from orion.communication.service import ComunicacaoService  # noqa: E402
from orion.communication.transport import TcpTransport  # noqa: E402
from orion.display.avatar_server import AvatarServer  # noqa: E402
from orion.display.quadro import Quadro  # noqa: E402
from orion.ensino.autoconhecimento import Autoconhecimento  # noqa: E402
from orion.ensino.banco_aulas import BancoAulas, caminho_padrao  # noqa: E402
from orion.ensino.ementa import BancoEmentas  # noqa: E402
from orion.ensino.ementa import responder_pergunta as responder_pergunta_ementa  # noqa: E402
from orion.ensino.professor import Professor  # noqa: E402
from orion.kernel.config import ConfigurationManager  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.mission.ai_manager import AiManager  # noqa: E402
from orion.mission.alivio_carga import AlivioCarga  # noqa: E402
from orion.mission.conselheiro_comportamento import ConselheiroComportamento  # noqa: E402
from orion.mission.conselho_protocolo import (  # noqa: E402
    TOPICO_RESPOSTA,
    AtendenteConselhoIA,
)
from orion.mission.decisao_estrategica import PonteDecisao  # noqa: E402
from orion.mission.diario import DiarioObservacoes  # noqa: E402
from orion.mission.internet import ConsultaInternet  # noqa: E402
from orion.mission.memory_client import MemoryClient  # noqa: E402
from orion.mission.mission_planner import MissionPlanner  # noqa: E402
from orion.mission.jarvis_plugins import get_registry  # noqa: E402
from orion_api import set_mission_planner, app as orion_api_app  # noqa: E402
import uvicorn  # noqa: E402
from orion.mission.ponte_conselho import PonteConselho  # noqa: E402
from orion.mission.ponte_conversa import PonteConversa  # noqa: E402
from orion.mission.quem_fala import QuemEstaFalando  # noqa: E402
from orion.mission.recados import Recados, definir_apelidos  # noqa: E402
from orion.mission.saudacao import SaudacaoEspontanea  # noqa: E402
from orion.vision.modulo import ModuloVisao  # noqa: E402
from orion.voice.ouvidos_esp32 import OuvidosESP32  # noqa: E402
from orion.voice.sintese import Sintetizador  # noqa: E402
from orion.voice.transcricao import Transcritor  # noqa: E402
from orion.voice.vad import DetectorAtividadeSonora  # noqa: E402
from orion.voice.voice_core import VoiceCore  # noqa: E402
from orion.voice.wake_word import DetectorPalavraAtivacao  # noqa: E402
from orion.vision.modulo import (  # noqa: E402
    INTERVALO_RETENTATIVA_FAMILIA_S,
    MAX_TENTATIVAS_FAMILIA,
)

def _palavras_ativacao(secao_voz: dict) -> tuple[str, ...]:
    """Os nomes que acordam o robo, LIDOS DA CONFIG (voice.wake_word).

    Ate 2026-10-25 este arquivo tinha o "fofao" escrito aqui e nunca lia a
    config: o nome mudou para Javis no Pi em 19/09 e o Notebook continuou
    esperando o velho, sem ninguem conseguir acordar o robo. Aceita um nome
    ou uma lista, igual ao ModuloVoz (voice/modulo.py).
    """
    nomes = secao_voz["wake_word"]
    return (nomes,) if isinstance(nomes, str) else tuple(nomes)

# Mesma hora que o kernel/logger.py passou a usar em 2026-07-30: os dois logs
# (Notebook e Pi) precisam ser comparaveis linha a linha, senao correlacionar
# "a IA decidiu" com "o robo andou" continua sendo adivinhacao.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("conversar_jarvis")


class TranscritorComLog(Transcritor):
    """Transcritor que loga o volume (RMS) e o texto de cada janela - so
    para depuracao ao vivo: sem isso nao da para saber se o microfone esta
    captando fraco ou se o Whisper esta entendendo errado o nome dele.
    Vale para o vigia da ativacao tambem: toda janela que passa do VAD vira
    uma linha "ouvi (rms=...)". Se nenhuma aparece, o som nao passou do VAD."""

    async def transcrever(self, audio) -> str:  # type: ignore[override]
        import numpy as np

        rms = float(np.sqrt(np.mean(np.asarray(audio, dtype="float64") ** 2)))
        texto = await super().transcrever(audio)
        logger.info("ouvi (rms=%.4f): %r", rms, texto)
        return texto


async def principal() -> None:
    config = ConfigurationManager("config/orion.yaml").carregar()
    secao_voz = config.secao("voice")
    secao_ia = config.secao("ai")
    secao_visao = config.secao("vision")

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())

    servidor = AvatarServer(
        bus,
        config_frontend={
            "pan_limits_degrees": secao_visao["pan_limits_degrees"],
            "tilt_limits_degrees": secao_visao["tilt_limits_degrees"],
        },
        # Descanso de tela: vai a secao inteira, e nao um campo escolhido
        # aqui - quem sabe quais chaves usa e o `tela.py`. `.get("avatar")`
        # devolve None se a subsecao faltar, e None ali significa "valores
        # padrao", nao "sem descanso".
        config_tela=config.secao("display").get("avatar"),
    )
    await servidor.iniciar()
    await bus.publish("system.ready", {"robot_name": config.secao("system")["robot_name"]})
    logger.info("Avatar no ar em http://127.0.0.1:8090")

    # O ESP32 e a UNICA fonte de entrada em producao. Nao ha fallback local:
    # selecionar outro mic apos um reboot torna a falha invisivel e faz o robo
    # ouvir o lugar errado. O Voice Core so sobe quando os ouvidos I2S chegam.
    conf_ouvidos = secao_voz.get("ouvidos_esp32") or {}
    if not conf_ouvidos.get("habilitado"):
        raise RuntimeError("ouvidos_esp32 precisa ficar habilitado em producao")
    ouvidos_esp32 = OuvidosESP32(conf_ouvidos["host"], conf_ouvidos["porta"])
    await ouvidos_esp32.iniciar()
    while not ouvidos_esp32.conectado:
        logger.warning("OUVIDOS: aguardando ESP32 (%s:%d)", conf_ouvidos["host"], conf_ouvidos["porta"])
        await asyncio.sleep(3.0)
    gravar_audio = ouvidos_esp32.gravar
    logger.info("OUVIDOS: os dois mics I2S do ESP32 (%s:%d)", conf_ouvidos["host"], conf_ouvidos["porta"])

    # Freios contra invencao (ver transcricao.py). `.get` com default para nao
    # quebrar um orion.yaml mais antigo que ainda nao tenha a secao.
    conf_transcricao = secao_voz.get("transcricao") or {}
    logger.info("Carregando Whisper '%s' (a primeira vez demora)...", secao_voz["whisper_model"])
    transcritor = TranscritorComLog(modelo=secao_voz["whisper_model"], **conf_transcricao)
    logger.info("Carregando Whisper '%s' (vigia da wake word)...", secao_voz["whisper_model_ativacao"])
    transcritor_ativacao = TranscritorComLog(
        modelo=secao_voz["whisper_model_ativacao"], **conf_transcricao
    )

    sintetizador = Sintetizador(
        secao_voz["piper_voice_path"],
        indice_dispositivo_saida=secao_voz["saida_audio_indice"],
    )

    ia = AiManager(
        temperatura=secao_ia["temperature"],
        caminho_prompt_sistema=secao_ia["system_prompt_file"],
        max_tokens_resposta=secao_ia["resposta_max_tokens"],
        openai_model=secao_ia.get("openai_model", "gemini-flash-lite-latest"),
        openai_base_url=secao_ia.get("openai_base_url"),
    )
    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning(
            "OPENAI_API_KEY nao esta no ambiente - o robo vai dizer que esta sem IA "
            "em toda conversa (a reserva local saiu em 2026-08-05)"
        )

    # Conselheiro do maestro: o Pi pergunta "o que faco agora?" pelo Event
    # Bus e este atendente responde usando o mesmo Ollama local. A resposta
    # e limitada por schema JSON (nao da para inventar nome de
    # comportamento) e o Pi ainda revalida antes de acionar qualquer coisa.
    # Se isto falhar ou demorar, o maestro simplesmente segue pela regra.
    # UM conselheiro so, criado uma vez (corrigido em 2026-07-30).
    #
    # Havia DOIS, e o que ficava ativo apontava para o lugar errado. Este
    # aqui nascia com `modelo=secao_ia["ollama_model"]` ("gemma3:1b") e SEM
    # `base_url` - ou seja, tentava falar com a OpenAI oficial, usando a
    # chave do OpenRouter (sk-or-v1-...) e pedindo um modelo que nao existe
    # la. Falharia em toda chamada.
    #
    # Mais adiante a variavel `conselheiro` era REATRIBUIDA a um segundo
    # objeto, esse sim correto - mas o `AtendenteConselhoIA` ja tinha
    # guardado o metodo ligado ao PRIMEIRO. Reatribuir a variavel nao troca
    # o objeto de quem ja segurava uma referencia a ele.
    #
    # So nao dava problema visivel porque o topico que o atendente escuta
    # (`behavior.pedir_conselho`) so e publicado quando `conselho_ia` esta
    # habilitado no orion.yaml - e esta desligado. Bug latente esperando
    # alguem ligar a chave.
    conselheiro = ConselheiroComportamento(
        modelo=secao_ia.get("mentor_model", "gemini-flash-lite-latest"),
        base_url=secao_ia.get("openai_base_url"),
    )
    AtendenteConselhoIA(bus, conselheiro.aconselhar)

    # Link TCP com o Motion Core (Raspberry) - Cap 14 s.2. Tolerado ausente
    # (Cap 6 s.8): sem ele a conversa segue, mas comandos de hardware e a
    # memoria persistente ficam desligados. O supervisor abaixo conecta no
    # inicio e RECONECTA sempre que o link cair - sem isso, um restart do Pi
    # deixava o Notebook orfao para sempre (bug real, 2026-07-19).
    conf_comm = config.secao("communication")
    conf_raspberry = conf_comm["raspberry"]
    comm = ComunicacaoService(
        "mission_core",
        bus,
        max_retries=conf_comm["max_retries"],
        ack_timeout_ms=conf_comm["ack_timeout_ms"],
    )
    heartbeat = MonitorHeartbeat(
        comm,
        bus,
        intervalo_s=conf_comm["heartbeat_interval_s"],
        heartbeats_perdidos_limite=conf_comm["heartbeats_lost_threshold"],
    )

    # Receptor da replica cruzada do backup (Cap 15 s.6): o Pi manda os
    # blocos do backup diario por este mesmo link "motion_core" - registra
    # so uma vez (independe de reconexao, o event bus e o mesmo).
    conf_db = config.secao("database")
    if conf_db.get("replica_to_notebook"):
        receptor_replica = ReceptorReplica(conf_db["notebook_replica_dir"])
        receptor_replica.registrar(bus)
        logger.info(
            "Receptor de replica do backup ativo (destino: %s)",
            conf_db["notebook_replica_dir"],
        )

    # Ponte de decisao estrategica (EDR-0022): o comportamento IaEstrategica
    # no Pi manda "mission.decidir" por este mesmo link "motion_core" -
    # mesmo padrao do receptor de replica acima.
    PonteDecisao(ia, comm).registrar(bus)

    # Mentor de comportamento (ligado em 2026-07-24): comportamento Mentor
    # no Pi manda "behavior.aconselhar" por este mesmo link. Usa IA remota
    # (OpenAI/OpenRouter) - decisao do usuario: Ollama local trava o
    # Notebook ao carregar um modelo (mesmo achado do EDR-0021). Tolerado
    # ausente: sem OPENAI_API_KEY ou com a lib `openai` faltando, o Mentor
    # simplesmente nunca tem conselho (Cap 6 s.8), sem derrubar o boot.
    # REUSA o conselheiro criado acima, em vez de construir um segundo com
    # outros parametros (ver o comentario lá). Os dois caminhos - o local
    # (`AtendenteConselhoIA`, via Event Bus) e o de rede (`PonteConselho`, via
    # comm.request) - passam a consultar a MESMA IA, com o mesmo modelo e o
    # mesmo servidor. Duas instancias divergentes para a mesma pergunta era
    # exatamente o tipo de coisa que ninguem nota até dar resposta diferente.
    try:
        PonteConselho(conselheiro, comm).registrar(bus)
        logger.info("Mentor de comportamento ativo (IA remota)")
    except Exception:
        logger.exception("Mentor de comportamento indisponivel - seguindo sem ele")

    async def _conectar_link_pi() -> None:
        transporte = TcpTransport(conf_raspberry["host"], conf_raspberry["tcp_port"])
        await asyncio.wait_for(transporte.conectar(), timeout=3.0)
        # adicionar_link substitui o link "motion_core" pelo novo transporte
        # e FECHA o anterior (socket + tarefa de recepcao) - ver
        # _descartar_link_anterior em communication/service.py. Antes de
        # 2026-07-19 ele so sobrescrevia a entrada do dicionario e o socket
        # velho ficava vivo: 37 conexoes vazadas num pico de reconexoes.
        comm.adicionar_link("motion_core", transporte, alcancaveis_via=["hardware_core"])

    # o heartbeat publica comm.module_lost quando o link com o Pi cai.
    link_caiu = asyncio.Event()

    def _ao_perder_link(evento) -> None:
        if evento.dados.get("modulo") == "motion_core":
            link_caiu.set()

    bus.subscribe("comm.module_lost", _ao_perder_link)

    async def supervisor_link_pi() -> None:
        avisou_ausencia = False
        while True:
            while True:  # tenta ate conectar (inicio ou reconexao)
                try:
                    await _conectar_link_pi()
                    break
                except Exception:
                    if not avisou_ausencia:
                        logger.warning(
                            "Motion Core inalcancavel em %s:%d - tentando a cada 5s...",
                            conf_raspberry["host"],
                            conf_raspberry["tcp_port"],
                        )
                        avisou_ausencia = True
                    await asyncio.sleep(5)
            avisou_ausencia = False
            logger.info(
                "Motion Core conectado (%s:%d) - comandos de hardware e memoria ATIVOS",
                conf_raspberry["host"],
                conf_raspberry["tcp_port"],
            )
            link_caiu.clear()
            await link_caiu.wait()  # dorme ate o link cair
            logger.warning("Link com o Motion Core caiu - reconectando...")
            await asyncio.sleep(2)

    # O Pi e leve e estavel; o padrao curto serve. Mas le da config para
    # ficar ajustavel sem mexer no codigo (regra #6 do ARQUITETURA.txt).
    heartbeat.monitorar(
        "motion_core",
        heartbeats_perdidos_limite=(
            conf_comm.get("heartbeats_lost_threshold_por_link") or {}
        ).get("motion_core"),
    )
    tarefa_heartbeat = asyncio.create_task(heartbeat.iniciar())
    tarefa_link = asyncio.create_task(supervisor_link_pi())

    # Encaminha ao Pi os eventos de voz que o maestro (Behavior Core) usa
    # para o comportamento Atender: quando o dono chama "Fofão", o robô deve
    # parar o que faz e atender. local=False evita eco (o evento ja existe
    # no bus do Notebook - so precisa CHEGAR ao Pi).
    async def _repassar_ao_pi(evento) -> None:
        try:
            await comm.publish(evento.topico, dict(evento.dados), local=False)
        except Exception:
            logger.debug("falha ao repassar '%s' ao Pi", evento.topico, exc_info=True)

    # Resposta do conselheiro de comportamento: nasce aqui (o Ollama roda
    # no Notebook) e precisa CHEGAR ao maestro, que roda no Pi. Sem este
    # repasse o Pi pergunta e nunca ouve resposta - evento nao atravessa o
    # link sozinho, alguem tem que encaminhar (Cap 14 s.7).
    bus.subscribe(TOPICO_RESPOSTA, _repassar_ao_pi)

    bus.subscribe("voice.wake_detected", _repassar_ao_pi)
    bus.subscribe("voice.response_finished", _repassar_ao_pi)

    # "Fiquei surdo" precisa CHEGAR ao painel do Pi, que é a única tela de
    # diagnóstico do robô. O `voice.audio_error` nasce aqui (o VoiceCore roda
    # no Notebook) e o painel do Pi JÁ o assina desde sempre
    # (webui/server.py, TOPICOS_REPASSADOS) - só que ninguém o colocava no
    # fio. Resultado medido em 2026-08-14: o ESP32 dos ouvidos sumiu às 17:24,
    # o VoiceCore publicou este evento a cada ~7s por 3h35, e o painel do Pi
    # registrou ZERO. O robô gritava que estava surdo e o grito morria no
    # barramento local. Mesma família de "publicar no vazio" dos dois repasses
    # logo abaixo - a 4ª vez que ela morde.
    bus.subscribe("voice.audio_error", _repassar_ao_pi)

    # alerta de rosto desconhecido nasce aqui (Sentinela de visão) e vai ao
    # Pi, onde a Vigília (maestro) assume.
    bus.subscribe("sentinela.alerta", _repassar_ao_pi)

    # Gestos nascem aqui (camera + MediaPipe rodam no Notebook) e o maestro
    # que reage a eles roda no Pi. Sem este repasse o evento ficava preso no
    # barramento do Notebook - a mesma familia de bug de "publicar no vazio"
    # que ja mordeu 3 vezes: publicar nunca falha, entao nada denuncia.
    bus.subscribe("gesto.detectado", _repassar_ao_pi)

    # Presenca de pessoa para o Pi: o comportamento AcompanharPessoa (segura a
    # cabeca enquanto a visao rastreia) e o EstadoDoMundo (contexto que a IA
    # le) dependem disto.
    #
    # Ate 2026-07-28 NADA de visao era encaminhado. O EstadoDoMundo do Pi
    # assinava vision.person_detected/vision.person_lost e nunca recebia nada,
    # entao `pessoa_presente` era sempre falso e o conselheiro decidia como se
    # a sala estivesse permanentemente vazia - sem nenhum erro no log, porque
    # assinar um topico que ninguem publica nao falha.
    #
    # ESTRANGULADO A 1/s DE PROPOSITO: vision.person_detected e publicado uma
    # vez POR PESSOA POR QUADRO (ate 15x/s). Despejar isso no link TCP afogaria
    # o enlace e atrasaria os heartbeats - que e exatamente como o sistema
    # detecta falha, e o mesmo erro que a telemetria completa cometeu com o
    # ultrassom (docs/journal.md, 2026-07-27). Quem precisa do dado quer saber
    # "tem alguem ai?", nao a caixa de cada quadro.
    #
    # vision.person_lost NAO e estrangulado: ele ja e publicado uma unica vez,
    # na transicao, e atrasar "perdi a pessoa" e pior que atrasar "ainda esta
    # aqui".
    INTERVALO_PRESENCA_S = 1.0
    ultimo_repasse_presenca = {"ts": 0.0}

    async def _repassar_presenca(evento) -> None:
        agora = time.monotonic()
        if agora - ultimo_repasse_presenca["ts"] < INTERVALO_PRESENCA_S:
            return
        ultimo_repasse_presenca["ts"] = agora
        await _repassar_ao_pi(evento)

    async def _repassar_perda(evento) -> None:
        # Zera o estrangulador: a proxima pessoa que aparecer deve chegar ao
        # Pi na hora, sem esperar o intervalo da deteccao anterior.
        ultimo_repasse_presenca["ts"] = 0.0
        await _repassar_ao_pi(evento)

    bus.subscribe("vision.person_detected", _repassar_presenca)
    bus.subscribe("vision.person_lost", _repassar_perda)

    # QUEM e a pessoa - e nao so que ha uma. Ligado em 2026-07-29, e a falta
    # dele deixava TRES coisas mortas ao mesmo tempo no Pi:
    #
    #   - FOLLOW: `NavigationCore._ao_reconhecer_pessoa` assina exatamente este
    #     topico desde 2026-07-28, quando o modo foi corrigido para so andar
    #     atras de gente RECONHECIDA. So que ninguem encaminhava o evento, e o
    #     handler nunca rodou uma vez sequer. A correcao de 28/07 trocou o
    #     assinante e esqueceu o produtor - o mesmo erro que ela mesma
    #     consertava.
    #   - `EstadoDoMundo.pessoa_conhecida`, que ficava falso ate para os donos
    #     da casa, e a IA decidia achando que so entravam estranhos.
    #   - o Diario, que so grava quem tem nome.
    #
    # Estrangulado como a presenca, e pelo mesmo motivo medido em 27/07: este
    # evento sai uma vez por quadro rastreado (ate 15x/s) e despeja-lo no link
    # TCP atrasa os heartbeats, que e como o sistema detecta falha.
    #
    # RESSALVA HONESTA: 1/s e grosseiro para o FOLLOW, que usa o `centro_x`
    # daqui para decidir o giro - a 1 leitura por segundo o robo corrige a
    # direcao devagar. Nao inventei um numero melhor porque nenhum foi medido;
    # se o seguimento ficar lento na bancada, este e o numero a revisitar, com
    # medicao e nao com palpite.
    ultimo_repasse_identidade = {"ts": 0.0}

    async def _repassar_identidade(evento) -> None:
        agora = time.monotonic()
        if agora - ultimo_repasse_identidade["ts"] < INTERVALO_PRESENCA_S:
            return
        ultimo_repasse_identidade["ts"] = agora
        await _repassar_ao_pi(evento)

    bus.subscribe("vision.person_recognized", _repassar_identidade)

    async def enviar_comando_hardware(comando: str) -> None:
        """Manda um COMMAND ao Mega pela cadeia TCP->serial e aguarda o ACK."""
        assert comm is not None
        await comm.send("hardware_core", {"comando": comando})
        logger.info("Comando '%s' entregue ao Hardware Core (ACK recebido)", comando)

    async def mover_cabeca(pan: float, tilt: float) -> None:
        """Move os servos da cabeca a pedido de quem esta CONVERSANDO.

        SAO DUAS COISAS, e omitir a segunda e o bug conhecido deste projeto.

        1. `comm.send` move o servo de verdade.
        2. `bus.publish` avisa a VISAO. Ela guarda uma estimativa em software
           de onde a cabeca esta (`CalculadoraPanTilt`) e calcula a proxima
           correcao do rastreamento a partir dela. Quem move a cabeca por fora
           sem avisar deixa essa estimativa mentindo, e a correcao seguinte sai
           de uma base que nao existe - e o "pan/tilt fica maluco depois de
           horas funcionando bem". O `VisionCore` ja escuta este topico e
           ressincroniza sozinho: basta a origem NAO ser a dele.

        A ordem e essa de proposito: primeiro o mundo, depois quem o observa.
        Se o `send` falhar, o servo nao se mexeu e a visao continua com a
        estimativa antiga - que e a correta, justamente porque nada mudou.
        """
        assert comm is not None
        await comm.send(
            "hardware_core",
            {"comando": "SET_PAN_TILT", "pan_graus": pan, "tilt_graus": tilt},
        )
        await bus.publish("motion.pan_tilt", {"pan": pan, "tilt": tilt, "origem": "conversa"})
        logger.info("cabeca movida a pedido falado: pan=%.1f tilt=%.1f", pan, tilt)

    def _ram_livre_mb() -> int:
        """RAM disponivel do Notebook em MB, lida de /proc/meminfo
        (MemAvailable - o que o kernel estima que da pra usar sem swap)."""
        with open("/proc/meminfo", encoding="ascii") as arquivo:
            for linha in arquivo:
                if linha.startswith("MemAvailable:"):
                    return int(linha.split()[1]) // 1024
        return -1

    async def reportar_saude_ao_pi() -> None:
        """Publica a RAM livre do Notebook para o Guardião de RAM no Pi
        (EDR-0020) agir antes de um travamento por falta de memoria."""
        intervalo = config.secao("behavior")["notebook_health_interval_s"]
        while True:
            try:
                await comm.publish("diagnostic.notebook_health", {"ram_livre_mb": _ram_livre_mb()})
            except Exception:
                logger.debug("falha ao reportar saude ao Pi (link caiu?)", exc_info=True)
            await asyncio.sleep(intervalo)

    # Diario (camada 2 da cognicao): escuta o que a visao ve e grava; o
    # planner le de volta para o grounding. Sem ele o bloco de observacoes
    # chegava sempre vazio - o robo era honesto por nao ter memoria, nao
    # por ter olhado.
    # Quem vive nesta casa e o que cada um e PARA ELE. Carregado AQUI, antes
    # de tudo o que consome: a saudacao, a conversa (MissionPlanner) e a IA
    # estrategica usam a MESMA lista - uma fonte so, para o robo nao ter duas
    # ideias de quem mora aqui.
    try:
        familia_conf = config.secao("familia")
    except Exception:
        familia_conf = []
        logger.warning("secao `familia` ausente no orion.yaml - o robo fala sem saber de quem")

    diario = DiarioObservacoes(bus, MemoryClient(comm))

    # A caixa de recados. Uma so no robo, compartilhada por quem GUARDA (o
    # planner, quando alguem pede) e por quem ENTREGA (a saudacao, quando a
    # pessoa chega) - ver orion/mission/recados.py.
    # Os apelidos vem da MESMA lista de familia lida acima - uma fonte so.
    # Sem esta chamada, "Marisane" e "Marah" voltam a ser pessoas diferentes
    # para a caixa de recados.
    definir_apelidos(familia_conf)
    recados = Recados(MemoryClient(comm))

    async def guardar_conhecimento(titulo: str, conteudo: str) -> bool:
        """Escreve um fato no vault de longo prazo, no SSD do Pi.

        O VAULT EXISTIA E ESTAVA VAZIO. Desde 23/07 havia `nota_escrever`
        funcionando ponta a ponta - cliente, ponte e disco - e NENHUM codigo do
        robo chamava. A pasta tinha zero notas, e o `nota_buscar` que roda a
        cada conversa procurava numa gaveta vazia. Mesmo padrao do GOAP orfao:
        caminho pronto, ninguem passando por ele.

        Devolve se conseguiu, e nao levanta: quem chama transforma isso em
        "guardei" ou "nao consegui guardar". Dizer que guardou sem ter guardado
        e o defeito que o recado da Mara ja custou uma vez.
        """
        try:
            await MemoryClient(comm).nota_escrever(titulo, conteudo)
        except Exception:
            logger.warning("nao consegui gravar a nota %r no vault", titulo, exc_info=True)
            return False
        logger.info("conhecimento guardado no vault: %r", titulo)
        return True

    # A internet como fonte sobre o MUNDO (2026-08-07). A acao `buscar` do
    # catalogo cai aqui; o achado ja vem com a fonte colada e vira a fala
    # direto, sem uma segunda chamada ao modelo para reescreve-lo.
    consulta_internet = ConsultaInternet()

    # O PROFESSOR (2026-08-07). Da aula a partir do banco local, SEM chamar
    # modelo: pedido do dono depois de a cota diaria da IA acabar e o robo
    # ficar sem fazer nada. O banco e um indice construido de
    # `content/aulas/*.yaml` por `tools/aulas.py construir` - se o arquivo
    # nao existir, `procurar` nao acha nada e o pedido segue para a conversa
    # normal, que e o degrau seguinte da cascata.
    banco_aulas = BancoAulas(caminho_padrao(_RAIZ_PROJETO))
    professor = Professor(banco_aulas, Quadro(bus), sintetizador.falar, bus)

    # O MAPA do que ele ensina. Os ids das aulas escritas sao lidos a cada
    # pergunta, e nao guardados aqui: `tools/aulas.py construir` pode rodar
    # com o robo no ar, e uma lista congelada na subida faria ele negar uma
    # aula que ja existe no banco.
    banco_ementas = BancoEmentas(caminho_padrao(_RAIZ_PROJETO))

    def responder_ementa(texto: str):
        return responder_pergunta_ementa(
            banco_ementas, texto, {a.id for a in banco_aulas.listar()}
        )

    planner = MissionPlanner(
        ia,
        professor=professor,
        # O que ele sabe do proprio corpo, do mesmo saber.db. Vem ANTES da
        # IA na cascata: sobre o proprio hardware o modelo inventa.
        autoconhecimento=Autoconhecimento(caminho_padrao(_RAIZ_PROJETO)),
        ementas=responder_ementa,
        consultar_internet=consulta_internet.consultar,
        enviar_comando_hardware=enviar_comando_hardware,
        memory_client=MemoryClient(comm),
        diario=diario,
        # A mesma `familia` que a saudacao e a IA estrategica usam - uma fonte
        # so, para o robo nao ter duas ideias de quem vive nesta casa.
        familia=familia_conf,
        # "olha pra esquerda" (2026-08-03). Sem este fio, o catalogo de acoes
        # tem a cabeca e a cabeca nao se mexe - o robo responderia "ainda nao
        # consigo fazer isso", que e o que o codigo deve dizer quando nao ha
        # caminho, mas nao e o que se quer ouvir.
        mover_cabeca=mover_cabeca,
        # Guardar de verdade o que ele promete guardar (2026-08-03).
        deixar_recado=recados.deixar,
        # "vamos inserir conhecimento pra ele" (2026-08-04): o fio que faltava
        # entre a conversa e o vault de longo prazo.
        guardar_conhecimento=guardar_conhecimento,
        # JARVIS Plugins: code_executor, file_operations, system_control, etc.
        jarvis_plugins=get_registry(),
    )


    # QUEM ESTA FALANDO (2026-08-03). Ate aqui `processar()` recebia
    # `pessoa_id` como parametro e ninguem preenchia: as 152 conversas do banco
    # tinham `pessoa_id = NULL`, TODAS. A visao reconhecia o rosto, a tabela
    # `pessoas` tinha o id, e os dois nunca se encontravam - entao a memoria do
    # robo era da CASA e nao de cada pessoa. Numa casa com o dono, a cuidadora
    # e uma crianca de 3 anos, isso mistura os tres no mesmo balaio.
    quem_fala = QuemEstaFalando(bus, MemoryClient(comm))

    set_mission_planner(planner, quem_fala)

    def limpar_para_fala(texto: str) -> str:
        """Tira o que nao da para falar: emojis, markdown (*negrito*),
        quebras de linha viram pausa. O gemma3 adora um emoji."""
        import re

        texto = re.sub(r"[*_#`]", "", texto)
        texto = "".join(c for c in texto if c.isascii() or c.isalpha() or c in "áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ ")
        return re.sub(r"\s+", " ", texto).strip()

    async def processar_comando(texto: str) -> str:
        if not texto.strip():
            return "Não entendi, pode repetir?"
        logger.info("Voce disse: %s", texto)
        # Fluxo do Cap 7 s.4: comandos predefinidos e hora sao resolvidos
        # pelo Mission Planner (rapido, deterministico, chega ao Mega);
        # so a conversa livre vai para a IA.
        resposta = limpar_para_fala(await planner.processar(texto, quem_fala.pessoa_id()))
        logger.info("Jarvis respondeu: %s", resposta)
        return resposta

    async def processar_comando_em_frases(texto: str):
        """Mesma resposta de `processar_comando`, entregue frase a frase.

        E o que permite o robo comecar a falar antes de a resposta terminar de
        ser gerada (T5.a, 2026-07-30): medido pareado com o prompt real, 3,33 s
        -> 1,84 s ate o primeiro som (-45%).

        `limpar_para_fala` roda POR FRASE, e nao no fim: cada frase vai direto
        para o Piper assim que chega, entao ela precisa estar limpa nesse
        instante - deixar para limpar o texto inteiro depois nao serviria de
        nada, o audio ja teria saido.
        """
        if not texto.strip():
            yield "Não entendi, pode repetir?"
            return
        logger.info("Voce disse: %s", texto)
        async for frase in planner.responder_em_frases(texto, quem_fala.pessoa_id()):
            limpa = limpar_para_fala(frase)
            if limpa:
                logger.info("Jarvis respondeu (frase): %s", limpa)
                yield limpa

    conf_vad = secao_voz["vad"]
    detector_atividade = None
    if conf_vad["habilitado"]:
        detector_atividade = DetectorAtividadeSonora(
            fator_acima_do_ruido=conf_vad["fator_acima_do_ruido"],
            rms_minimo=conf_vad["rms_minimo"],
            janelas_de_historico=conf_vad["janelas_de_historico"],
        )
        logger.info(
            "VAD ligado: Whisper de vigilancia so roda com som acima do ruido de fundo"
        )

    # IDENTIFICACAO DE LOCUTOR POR VOZ (2026-08-19): quem fala no microfone
    # (mesmo fora do quadro da camera) passa a ter a conversa creditada - o
    # `quem_fala` assina `voice.person_recognized` junto com o rosto. Sem
    # voiceprint cadastrado a identificacao devolve None sempre (conversa
    # "da casa"), sem derrubar o boot.
    identificador_locutor = None
    conf_ident = secao_voz.get("identificacao_locutor") or {}
    if conf_ident.get("habilitado"):
        from orion.voice.identificador_locutor import IdentificadorLocutor

        identificador_locutor = IdentificadorLocutor(
            limiar_cosseno=conf_ident.get("limiar_cosseno", 0.5),
            fonte_modelo=conf_ident.get("modelo", "speechbrain/spkrec-ecapa-voxceleb"),
            savedir=conf_ident.get("savedir"),
        )
        logger.info(
            "Identificacao de locutor por voz ativa (limiar cosseno %.3f)",
            conf_ident.get("limiar_cosseno", 0.5),
        )

    voz = VoiceCore(
        event_bus=bus,
        indice_microfone=-1,  # callback do ESP32 e a unica entrada permitida
        transcritor=transcritor,
        sintetizador=sintetizador,
        processar_comando=processar_comando,
        processar_comando_em_frases=processar_comando_em_frases,
        detector_palavra_ativacao=DetectorPalavraAtivacao(_palavras_ativacao(secao_voz)),
        exigir_palavra_ativacao=secao_voz["exigir_palavra_ativacao"],
        frase_ativacao="Oi? Pode falar!",
        transcritor_ativacao=transcritor_ativacao,
        detector_atividade=detector_atividade,
        # Fonte obrigatoria: nunca usar o microfone local apos reinicio.
        gravar_audio=gravar_audio,
        identificador_locutor=identificador_locutor,
    )

    # CHAT POR TEXTO (2026-08-03), pedido do dono: "vai q estou em outro lugar
    # e quero mandar ele ir na cozinha". A caixa de texto da aba Conversa (no
    # Pi) chega aqui por comm.request e entra no MESMO `processar_comando` que
    # uma transcricao do microfone usaria.
    #
    # Fica DEPOIS do VoiceCore de proposito: precisa de `processar_comando` e
    # de `voz`, que so existem a esta altura.
    async def atender_texto(texto: str) -> str:
        resposta = await processar_comando(texto)
        # Falar e decisao DAQUI, nao da ponte: a ponte so leva e traz texto.
        # Em segundo plano para a tela de quem digitou nao ficar esperando o
        # audio terminar - a resposta escrita ja esta pronta neste ponto.
        if resposta:
            asyncio.create_task(voz.falar(resposta))
        return resposta

    PonteConversa(atender_texto, comm).registrar(bus)
    logger.info("Chat por texto ativo (webui -> mission.conversa)")

    tarefa_saude = asyncio.create_task(reportar_saude_ao_pi())

    async def carregar_vozes_da_familia() -> None:
        """As vozes moram no banco do Pi - insiste ate o enlace responder.

        Mesma corrida de boot dos rostos (vision/modulo.py, `_insistir_
        familia`): ate 2026-10-25 a carga era UMA tentativa, feita no mesmo
        segundo em que o enlace subia, e falhava com "Sem rota conhecida" -
        o robo passava o dia sem reconhecer ninguem pela voz. Mesmos numeros
        da visao, e o mesmo desistir alto no fim.
        """
        for tentativa in range(1, MAX_TENTATIVAS_FAMILIA + 1):
            try:
                identificador_locutor.carregar_vozes_conhecidas(
                    await MemoryClient(comm).recall("pessoas", limite=50)
                )
                logger.info("Vozes da familia carregadas na tentativa %d", tentativa)
                return
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vozes da familia ainda nao (tentativa %d) - o enlace com o Pi pode "
                    "estar subindo", tentativa, exc_info=tentativa == 1,
                )
            await asyncio.sleep(INTERVALO_RETENTATIVA_FAMILIA_S)
        logger.error(
            "Desisti de carregar as vozes apos %d tentativas - ninguem sera reconhecido "
            "pela voz ate reiniciar", MAX_TENTATIVAS_FAMILIA,
        )

    tarefa_vozes = (
        asyncio.create_task(carregar_vozes_da_familia())
        if identificador_locutor is not None else None
    )

    orion_api_server = uvicorn.Server(
        uvicorn.Config(orion_api_app, host='0.0.0.0', port=8081, log_level='info')
    )
    tarefa_orion_api = asyncio.create_task(orion_api_server.serve())

    # Sentinela de visão (Cap 8): vigia rostos desconhecidos e dispara
    # sentinela.alerta -> Pi -> Vigília. Tolera câmera/link ausentes.
    # Visao (EDR-0023): o ModuloVisao e o dono da camera e traz VisionCore +
    # Sentinela juntos. A Sentinela deixou de abrir camera propria e de rodar
    # um SEGUNDO reconhecimento facial sobre os mesmos frames - agora ela
    # apenas ouve `vision.faces_desconhecidas` publicado pelo Vision Core.
    #
    # Efeito colateral esperado: o YOLO passa a carregar tambem aqui, o que
    # consome mais RAM do Notebook. O guardiao de RAM (EDR-0020) continua
    # vigiando; se apertar, e este modulo que deve ser pausado primeiro.
    modulo_visao = ModuloVisao(
        bus,
        secao_visao,
        conf_sentinela=config.secao("behavior")["sentinela_visao"],
        carregar_conhecidos=lambda: MemoryClient(comm).recall("pessoas"),
        comm=comm,
    )
    try:
        await modulo_visao.iniciar()
    except Exception:
        # Cap 6 s.8: sem camera ou sem os modelos, a conversa segue de pe.
        logger.exception("Visao indisponivel - seguindo sem ela")

    # A VISAO RESPONDE "O QUE E ISSO?" (2026-08-09). O planner foi montado
    # ANTES do modulo de visao (a camera carrega o YOLO no iniciar), entao o
    # fio e ligado aqui, DEPOIS de a visao estar no ar: quem pergunta o que
    # ha na frente recebe o que a camera ve agora - e nao o diario da meia
    # hora atras, nem a IA achando no escuro (o modelo nao ve o frame).
    planner.registrar_consultar_visao(modulo_visao.ver_agora)

    # Atende os pedidos de alivio do Guardiao de RAM que roda no Pi. Ate
    # 2026-07-19 esse pedido era publicado e ninguem escutava - a protecao
    # contra travamento por falta de memoria existia so no papel.
    # A visao entra como sacrificavel; a VOZ nao: se o dono chamar "Fofao"
    # durante um aperto de memoria, o robo tem que responder.
    #
    # AS DUAS PONTAS ESTAVAM MORTAS ate 2026-07-30, e o mecanismo inteiro
    # entregava ZERO bytes sem nunca dar erro:
    #
    #   - `pausar_visao=None`: o maior consumidor do Notebook (YOLO +
    #     reconhecimento facial) nunca era pausado. O ModuloVisao agora expoe
    #     pausar()/retomar(), fechando a pendencia declarada no merge de
    #     26/07 - eram os dois metodos que faltavam, nada mais;
    #   - `ia.descarregar` levantava AttributeError SEMPRE, porque usava um
    #     atributo (`self._cliente`) que nunca existiu na classe. O
    #     `AlivioCarga` captura e loga cada acao isoladamente, entao o log
    #     mostrava o caminho feliz inteiro - detecta, pede, atende - com nada
    #     acontecendo no fim.
    #
    # `descarregar_modelo` NAO E MAIS PASSADO (2026-08-05): com o Ollama fora,
    # nao ha modelo local na RAM para tirar. Passar algo que nao libera nada
    # seria refazer o bug acima com outra fachada. O alivio ficou com a visao,
    # que sempre foi o maior consumidor da maquina.
    AlivioCarga(
        bus,
        pausar_visao=modulo_visao.pausar,
        retomar_visao=modulo_visao.retomar,
    )

    # SAUDACAO ESPONTANEA (2026-07-29, pedido do dono: "praticamente ao me
    # ver me cumprimentar"). So foi possivel hoje: ate esta sessao o nome da
    # pessoa nao chegava a lugar nenhum - o robo via o dono todo dia e
    # registrava "vi uma pessoa que nao reconheci".
    async def _compor_saudacao(
        nome: str, descricao: str, contexto: str, evitar: list[str]
    ) -> str:
        """Deixa a IA escolher as palavras (pedido do dono, 2026-07-29).

        Pede SO a saudacao, curta, e nunca uma acao: uma IA solta aqui
        prometeria coisas que o robo nao faz. O limite continua sendo o
        `contrato.py`; este prompt so cuida do TOM.

        O `contexto` foi acrescentado no MESMO DIA, depois do dono ouvir o
        resultado: *"ele so fala 3 frases repetidas"*. A IA compunha - o log
        provava - mas o prompt tinha so nome e descricao, que nao mudam nunca.
        Mandar "seja natural" sem dar UM FATO que mude entre uma vez e outra
        produz a mesma frase toda vez, e foi o que aconteceu. A variedade nao
        vem de pedir variedade; vem de haver sobre o que falar.
        """
        quem = f"{nome} ({descricao})" if descricao else nome
        # As frases ja usadas com esta pessoa. E o que ataca a repeticao sem
        # recitar dado nenhum: "nao repita estas" forca palavra nova, enquanto
        # pedir "seja variado" no vazio nao forca coisa alguma (medido).
        if evitar:
            ja_ditas = "\n".join(f'- "{f}"' for f in evitar)
            proibicao = (
                f"\n\nVoce JA disse estas frases a esta pessoa. Diga algo "
                f"claramente DIFERENTE, com outras palavras e outra "
                f"abertura:\n{ja_ditas}"
            )
        else:
            proibicao = ""
        return await ia.responder(
            f"Cumprimente {quem}, que acabou de aparecer na sua frente.\n\n"
            f"O momento agora:\n{contexto}{proibicao}\n\n"
            f"Uma frase curta, calorosa e natural, como quem reencontra "
            f"alguem de casa.\n\n"
            f"O momento acima serve para voce ESCOLHER O TOM, nao para ser "
            f"contado. NAO diga a hora, NAO recite esses dados e NAO comente "
            f"quanto tempo passou - ninguem pede a hora ao ser cumprimentado. "
            f"So deixe que eles mudem o jeito de falar, para o cumprimento nao "
            f"sair sempre igual. NAO repita sempre a mesma abertura. "
            f"Nao ofereca nem prometa nenhuma acao; so cumprimente. "
            f"Responda apenas com a frase.",
        )

    SaudacaoEspontanea(
        bus,
        sintetizador.falar,
        familia=familia_conf,
        compor=_compor_saudacao,
        # O que ele viu hoje entra no cumprimento. E o mesmo diario que o
        # planner le - uma fonte so, para o robo nao ter duas memorias do
        # proprio dia que possam discordar.
        observacoes=diario.observacoes_de_hoje,
        # "avise a Mara quando a vir" (2026-08-03). O MESMO objeto que o
        # planner usa para guardar: uma so caixa de recados no robo. Duas
        # instancias funcionariam igual - as duas falam com o mesmo banco -
        # mas seriam duas ideias do que e um recado, e a proxima mudanca
        # precisaria lembrar das duas.
        recados=recados,
        # Sobrevive a restart: sem isto cada reinicio do avatar fazia ele
        # cumprimentar de novo, com a mesma frase (2026-10-31).
        arquivo_estado=Path("data/saudacao_estado.json"),
    )
    logger.info(
        "Saudacao espontanea ativa para: %s",
        ", ".join(p.get("nome", "?") for p in (familia_conf or [])) or "(ninguem)",
    )

    await sintetizador.falar("Oi! Eu sou o Jarvis. Pode falar comigo.")
    if secao_voz["exigir_palavra_ativacao"]:
        logger.info(
            'Pronto! Diga "%s" e depois o seu comando. Ctrl+C para sair.',
            _palavras_ativacao(secao_voz)[0].capitalize(),
        )
    else:
        logger.info("Pronto! Sem palavra de ativacao: e so falar. Ctrl+C para sair.")

    try:
        await voz.executar()
    finally:
        voz.parar()
        tarefa_saude.cancel()
        if tarefa_vozes is not None:
            tarefa_vozes.cancel()
        tarefa_orion_api.cancel()
        # PRIMEIRO o planner: ele ainda pode ter turnos por destilar, e a
        # destilacao precisa do link com o Pi (que o `comm.encerrar()` logo
        # abaixo derruba) para gravar o fato. Encerrar na ordem errada perderia
        # exatamente o que este passo existe para salvar.
        await planner.encerrar()
        # Fecha o socket dos ouvidos e mata a tarefa de drenagem. Sem isto
        # ficaria uma tarefa órfã tentando reconectar para sempre - o mesmo
        # defeito que a `tarefa_mentor` teve no Motion Core.
        if ouvidos_esp32 is not None:
            await ouvidos_esp32.encerrar()
        await modulo_visao.encerrar()
        tarefa_link.cancel()
        heartbeat.parar()
        tarefa_heartbeat.cancel()
        await comm.encerrar()
        await servidor.encerrar()
        bus.parar()
        tarefa_bus.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        pass
