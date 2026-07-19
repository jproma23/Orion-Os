"""Conversa de voz ao vivo com o Fofão no Notebook (Caps 7, 9 e 13).

Junta num processo só, no mesmo Event Bus:
  - AvatarServer (Cap 13) na porta 8090 - o kiosk do Firefox na TV mostra
    o avatar reagindo aos estados de voz de verdade;
  - VoiceCore (Cap 9) - escuta o microfone, espera a palavra "Fofão",
    transcreve o comando com o Whisper;
  - AiManager (Cap 7) - responde com o Ollama local;
  - Sintetizador (Cap 9) - fala a resposta pela TV (HDMI).

E o substituto ao vivo do tools/preview_avatar.py (que so simula eventos):
pare o servico do preview antes de rodar este script, senao a porta 8090
ja estara ocupada:

    systemctl --user stop orion-avatar.service
    cd ~/orion-os && .venv/bin/python tools/conversar_fofao.py

Fluxo de uso: espere o "pronto" no log (a primeira carga do Whisper pode
demorar), diga "Fofão" perto do microfone, aguarde o avatar mudar para
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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orion.communication.heartbeat import MonitorHeartbeat  # noqa: E402
from orion.communication.service import ComunicacaoService  # noqa: E402
from orion.communication.transport import TcpTransport  # noqa: E402
from orion.display.avatar_server import AvatarServer  # noqa: E402
from orion.kernel.config import ConfigurationManager  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.mission.ai_manager import AiManager  # noqa: E402
from orion.mission.memory_client import MemoryClient  # noqa: E402
from orion.mission.mission_planner import MissionPlanner  # noqa: E402
from orion.voice.captura_audio import SeletorMicrofone  # noqa: E402
from orion.voice.sintese import Sintetizador  # noqa: E402
from orion.voice.transcricao import Transcritor  # noqa: E402
from orion.voice.vad import DetectorAtividadeSonora  # noqa: E402
from orion.voice.voice_core import VoiceCore  # noqa: E402
from orion.voice.wake_word import DetectorPalavraAtivacao  # noqa: E402

# Grafias que o Whisper realmente produziu para "Fofão" nos testes ao vivo
# (2026-07-18): "Fafão", "furacão", "Japão", "falfão"... Lista fixa virou
# enxugar gelo - alem dela, o DetectorFuzzy abaixo aceita qualquer palavra
# a ate 2 edicoes de distancia de "fofao". Ate existir um modelo de wake
# word treinado de verdade (ver wake_word.py), e o que evita o robo
# ignorar o dono.
VARIACOES_FOFAO = (
    "fofão", "fofao", "furacão", "furacao", "japão", "japao",
)


def _distancia_edicao(a: str, b: str) -> int:
    """Levenshtein simples (sem lib externa - strings de ~5 letras)."""
    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        atual = [i]
        for j, cb in enumerate(b, 1):
            atual.append(min(
                anterior[j] + 1,          # remocao
                atual[j - 1] + 1,         # insercao
                anterior[j - 1] + (ca != cb),  # troca
            ))
        anterior = atual
    return anterior[-1]


class DetectorFuzzy(DetectorPalavraAtivacao):
    """Alem das variacoes exatas, acorda com qualquer palavra 'parecida'
    com fofao (distancia de edicao <= 2 apos tirar acentos)."""

    _ALVO = "fofao"

    def verificar(self, texto_transcrito: str) -> bool:
        if super().verificar(texto_transcrito):
            return True
        import re
        import unicodedata

        sem_acento = unicodedata.normalize("NFD", texto_transcrito.lower())
        sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
        return any(
            _distancia_edicao(palavra, self._ALVO) <= 2
            for palavra in re.findall(r"[a-z]+", sem_acento)
            if len(palavra) >= 4
        )

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("conversar_fofao")


class TranscritorComLog(Transcritor):
    """Transcritor que loga o volume (RMS) e o texto de cada janela - so
    para depuracao ao vivo: sem isso nao da para saber se o microfone esta
    captando fraco ou se o Whisper esta entendendo errado a palavra
    "Fofão"."""

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
    )
    await servidor.iniciar()
    await bus.publish("system.ready", {"robot_name": config.secao("system")["robot_name"]})
    logger.info("Avatar no ar em http://127.0.0.1:8090")

    logger.info("Escolhendo o melhor microfone (Cap 9 s.6)...")
    seletor = SeletorMicrofone(secao_voz["microfones_candidatos_indices"])
    indice_mic = await seletor.escolher_melhor()

    logger.info("Carregando Whisper '%s' (a primeira vez demora)...", secao_voz["whisper_model"])
    transcritor = TranscritorComLog(modelo=secao_voz["whisper_model"])
    logger.info("Carregando Whisper '%s' (vigia da wake word)...", secao_voz["whisper_model_ativacao"])
    transcritor_ativacao = TranscritorComLog(modelo=secao_voz["whisper_model_ativacao"])

    sintetizador = Sintetizador(
        secao_voz["piper_voice_path"],
        indice_dispositivo_saida=secao_voz["saida_audio_indice"],
    )

    ia = AiManager(
        modelo=secao_ia["ollama_model"],
        temperatura=secao_ia["temperature"],
        caminho_prompt_sistema=secao_ia["system_prompt_file"],
        max_tokens_resposta=secao_ia["resposta_max_tokens"],
        keep_alive_minutes=secao_ia["keep_alive_minutes"],
    )

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

    async def _conectar_link_pi() -> None:
        transporte = TcpTransport(conf_raspberry["host"], conf_raspberry["tcp_port"])
        await asyncio.wait_for(transporte.conectar(), timeout=3.0)
        # adicionar_link sobrescreve o link "motion_core" pelo novo transporte
        # ao reconectar (o antigo, morto, e descartado).
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

    heartbeat.monitorar("motion_core")
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

    bus.subscribe("voice.wake_detected", _repassar_ao_pi)
    bus.subscribe("voice.response_finished", _repassar_ao_pi)

    async def enviar_comando_hardware(comando: str) -> None:
        """Manda um COMMAND ao Mega pela cadeia TCP->serial e aguarda o ACK."""
        assert comm is not None
        await comm.send("hardware_core", {"comando": comando})
        logger.info("Comando '%s' entregue ao Hardware Core (ACK recebido)", comando)

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

    planner = MissionPlanner(
        ia,
        enviar_comando_hardware=enviar_comando_hardware,
        memory_client=MemoryClient(comm),
    )

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
        resposta = limpar_para_fala(await planner.processar(texto))
        logger.info("Fofao respondeu: %s", resposta)
        return resposta

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

    voz = VoiceCore(
        event_bus=bus,
        indice_microfone=indice_mic,
        transcritor=transcritor,
        sintetizador=sintetizador,
        processar_comando=processar_comando,
        detector_palavra_ativacao=DetectorFuzzy(VARIACOES_FOFAO),
        frase_ativacao="Oi? Pode falar!",
        transcritor_ativacao=transcritor_ativacao,
        detector_atividade=detector_atividade,
    )

    tarefa_saude = asyncio.create_task(reportar_saude_ao_pi())

    await sintetizador.falar("Oi! Pode falar comigo. É só me chamar de Fofão.")
    logger.info('Pronto! Diga "Fofão" e depois o seu comando. Ctrl+C para sair.')

    try:
        await voz.executar()
    finally:
        voz.parar()
        tarefa_saude.cancel()
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
