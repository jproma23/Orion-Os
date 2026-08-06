"""Testes do orquestrador Voice Core (Cap 9 s.3-5) - maquina de estados e
eventos. Usa fakes no lugar de microfone/Whisper/Piper reais.
"""
import asyncio

import pytest

pytest.importorskip("numpy")
pytest.importorskip("sounddevice")
pytest.importorskip("faster_whisper")
pytest.importorskip("piper")

from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.voice.voice_core import EstadoVoz, VoiceCore  # noqa: E402
from orion.voice.wake_word import DetectorPalavraAtivacao  # noqa: E402


class TranscritorFalso:
    def __init__(self, textos: list[str]) -> None:
        self._textos = list(textos)

    async def transcrever(self, audio) -> str:
        return self._textos.pop(0) if self._textos else ""


class SintetizadorFalso:
    def __init__(self) -> None:
        self.falas: list[str] = []

    async def falar(self, texto: str) -> None:
        self.falas.append(texto)


async def _gravar_audio_falso(duracao: float):
    return object()


async def _rodar_bus(bus: EventBus) -> asyncio.Task:
    return asyncio.create_task(bus.iniciar())


@pytest.mark.asyncio
async def test_sem_palavra_de_ativacao_volta_para_idle():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)

    async def processar(texto):
        return "nao deveria chamar"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["oi, tudo bem"]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
    )

    processou = await voice.ciclo_uma_vez()

    assert processou is False
    assert voice.estado is EstadoVoz.IDLE

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_fluxo_completo_com_palavra_de_ativacao():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    for topico in (
        "voice.wake_detected",
        "voice.command_received",
        "voice.transcription_ready",
        "voice.response_started",
        "voice.response_finished",
    ):
        bus.subscribe(topico, lambda e, t=topico: eventos.append(t))

    async def processar(texto):
        assert texto == "que horas sao"
        return "sao dez horas"

    sintetizador = SintetizadorFalso()
    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["fofao", "que horas sao"]),
        sintetizador=sintetizador,
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
    )

    processou = await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert processou is True
    assert voice.estado is EstadoVoz.IDLE
    assert sintetizador.falas == ["sao dez horas"]
    assert eventos == [
        "voice.wake_detected",
        "voice.command_received",
        "voice.transcription_ready",
        "voice.response_started",
        "voice.response_finished",
    ]

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_erro_no_processamento_ainda_fala_uma_resposta():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)

    async def processar_com_erro(texto):
        raise RuntimeError("IA fora do ar")

    sintetizador = SintetizadorFalso()
    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["fofao", "oi"]),
        sintetizador=sintetizador,
        processar_comando=processar_com_erro,
        gravar_audio=_gravar_audio_falso,
    )

    await voice.ciclo_uma_vez()

    assert len(sintetizador.falas) == 1
    assert "problema" in sintetizador.falas[0].lower()

    bus.parar()
    await tarefa


class SintetizadorComFalha:
    async def falar(self, texto: str) -> None:
        raise RuntimeError("PortAudio: dispositivo ocupado (simulado)")


@pytest.mark.asyncio
async def test_falha_ao_falar_nao_derruba_o_ciclo():
    """Achado real da vistoria de 2026-07-24: sintetizador.falar() nao
    tinha protecao nenhuma - um erro transitorio de audio de saida
    (PortAudio, dispositivo ocupado) derrubava o processo inteiro (voice
    core, sentinela, avatar - tudo compartilha o mesmo asyncio.run)."""
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    erros_audio = []
    bus.subscribe("voice.audio_error", lambda e: erros_audio.append(e.dados))

    async def processar(texto):
        return "oi"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["fofao", "oi"]),
        sintetizador=SintetizadorComFalha(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
    )

    processou = await voice.ciclo_uma_vez()  # nao deve levantar excecao
    await bus.aguardar_fila_vazia()

    assert processou is True
    assert len(erros_audio) == 1
    assert "PortAudio" in erros_audio[0]["motivo"]

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_erro_de_audio_publica_voice_audio_error():
    from orion.voice.captura_audio import ErroAudio

    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("voice.audio_error", lambda e: eventos.append(e.dados))

    async def gravar_com_erro(duracao):
        raise ErroAudio("microfone desconectado")

    async def processar(texto):
        return "nao deveria chegar aqui"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso([]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=gravar_com_erro,
    )

    processou = await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert processou is False
    assert voice.estado is EstadoVoz.ERROR
    assert len(eventos) == 1

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_deteccao_de_ativacao_customizada():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)

    async def processar(texto):
        return "ok"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["ei robo", "teste"]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
        detector_palavra_ativacao=DetectorPalavraAtivacao(palavras_ativacao=("robo",)),
    )

    processou = await voice.ciclo_uma_vez()

    assert processou is True

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_janela_silenciosa_pula_a_transcricao_de_vigilancia():
    """Regressao de custo (2026-07-19): com o portao VAD, uma janela sem
    som nao deve nem chegar ao transcritor de vigilancia - era o Whisper
    rodando continuamente que saturava a CPU do Notebook."""
    bus = EventBus()
    tarefa = await _rodar_bus(bus)

    class DetectorSempreSilencio:
        def tem_som(self, audio) -> bool:
            return False

    class TranscritorContador(TranscritorFalso):
        def __init__(self, textos):
            super().__init__(textos)
            self.chamadas = 0

        async def transcrever(self, audio) -> str:
            self.chamadas += 1
            return await super().transcrever(audio)

    async def processar(texto):
        return "nao deveria chamar"

    vigia = TranscritorContador(["fofão"])
    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso([]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
        transcritor_ativacao=vigia,
        detector_atividade=DetectorSempreSilencio(),
    )

    processou = await voice.ciclo_uma_vez()

    assert processou is False
    assert vigia.chamadas == 0  # Whisper poupado
    assert voice.estado is EstadoVoz.IDLE

    bus.parar()
    await tarefa


# ---------------------------------------------------------------------------
# Detector de laco do Whisper (2026-08-05).
#
# As frases abaixo NAO foram inventadas para o teste: sao transcricoes reais
# do log do `orion-avatar.service` de 05/08, quando ninguem estava falando.
# Naquele dia, de 905 janelas de audio, 175 sairam com texto e 27 estavam em
# laco - e o detector barrou 6. As tres primeiras aqui sao as que escapavam.
# ---------------------------------------------------------------------------


def test_laco_que_nao_comeca_na_primeira_palavra():
    """Escapava porque o detector ancorava em `palavras[:tamanho]`: so via o
    laco que comecasse na palavra 1. Aqui ele comeca na 4a."""
    texto = "Você não sabe " + "o que é " * 30
    assert VoiceCore._parece_alucinacao(texto)


def test_laco_que_muda_de_trecho_no_meio():
    """Real no log: comeca em 'o que é' e depois vira 'que é'. A ancora fixa
    parava de casar na terceira volta e o resto passava batido."""
    texto = "O que é o que é " + "que é " * 30
    assert VoiceCore._parece_alucinacao(texto)


def test_token_travado_numa_letra_so():
    """220 letras F numa 'palavra' so. Morria no piso `len(palavras) < 5` -
    uma palavra nunca chegava a ser examinada."""
    assert VoiceCore._parece_alucinacao("F" * 220)


def test_o_caso_original_continua_barrado():
    """O laco de 2026-08-02 que motivou o detector - a mudanca nao pode
    perde-lo de vista."""
    assert VoiceCore._parece_alucinacao("O que é " * 60)


def test_repeticao_de_uma_palavra_so():
    assert VoiceCore._parece_alucinacao("Não me dá, não me dá, não me dá, não me dá, não me dá.")


def test_fala_humana_nao_e_confundida_com_laco():
    """O risco da mudanca e o oposto: emudecer quem falou. A ancora agora
    desliza, entao frases normais passam a ser testadas em toda posicao."""
    for frase in [
        "Fofão, acende a luz da sala",
        "que horas o Kamal chega em casa",
        "você pode centralizar a sua cabeça para me ver",
        "sim, por favor",
        "Fofão! Fofão!",  # enfase humana: duas vezes, nao cinco
        "vai lá no quarto e vê se o Kamal está lá",
    ]:
        assert not VoiceCore._parece_alucinacao(frase), frase


def test_palavra_longa_legitima_nao_e_laco():
    """Nenhuma letra repetida 8 vezes seguidas - so palavra comprida."""
    assert not VoiceCore._parece_alucinacao("anticonstitucionalissimamente")
