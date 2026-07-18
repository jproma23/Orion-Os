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

Sem Raspberry envolvido (EDR-0018: Voice/Mission/Display sao do Notebook):
perguntas sao respondidas pela IA; comandos de hardware ("acende a
lanterna") ainda nao chegam ao robo por aqui - isso e o Mission Planner
completo, quando o link com o Motion Core estiver de pe.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orion.display.avatar_server import AvatarServer  # noqa: E402
from orion.kernel.config import ConfigurationManager  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.mission.ai_manager import AiManager  # noqa: E402
from orion.voice.captura_audio import SeletorMicrofone  # noqa: E402
from orion.voice.sintese import Sintetizador  # noqa: E402
from orion.voice.transcricao import Transcritor  # noqa: E402
from orion.voice.voice_core import VoiceCore  # noqa: E402
from orion.voice.wake_word import DetectorPalavraAtivacao  # noqa: E402

# Grafias que o Whisper realmente produziu para "Fofão" nos testes ao vivo
# (2026-07-18): "Fafão", "furacão" (e por seguranca as versoes sem til).
# Ate existir um modelo de wake word treinado de verdade (ver wake_word.py),
# aceitar as variacoes proximas evita o robo ignorar o dono.
VARIACOES_FOFAO = (
    "fofão", "fofao", "fafão", "fafao", "fufão", "fufao", "furacão", "furacao",
    "falfão", "falfao", "japão", "japao", "vovão", "vovao", "bofão", "bofao",
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
        resposta = limpar_para_fala(await ia.responder(texto))
        logger.info("Fofao respondeu: %s", resposta)
        return resposta

    voz = VoiceCore(
        event_bus=bus,
        indice_microfone=indice_mic,
        transcritor=transcritor,
        sintetizador=sintetizador,
        processar_comando=processar_comando,
        detector_palavra_ativacao=DetectorPalavraAtivacao(VARIACOES_FOFAO),
        frase_ativacao="Oi? Pode falar!",
        transcritor_ativacao=transcritor_ativacao,
    )

    await sintetizador.falar("Oi! Pode falar comigo. É só me chamar de Fofão.")
    logger.info('Pronto! Diga "Fofão" e depois o seu comando. Ctrl+C para sair.')

    try:
        await voz.executar()
    finally:
        voz.parar()
        await servidor.encerrar()
        bus.parar()
        tarefa_bus.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        pass
