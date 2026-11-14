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
        transcritor=TranscritorFalso(["jarvis", "que horas sao"]),
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
        transcritor=TranscritorFalso(["jarvis", "oi"]),
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
        transcritor=TranscritorFalso(["jarvis", "oi"]),
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

    vigia = TranscritorContador(["jarvis"])
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
        "Jarvis, acende a luz da sala",
        "que horas o Kamal chega em casa",
        "você pode centralizar a sua cabeça para me ver",
        "sim, por favor",
        "Jarvis! Jarvis!",  # enfase humana: duas vezes, nao cinco
        "vai lá no quarto e vê se o Kamal está lá",
    ]:
        assert not VoiceCore._parece_alucinacao(frase), frase


def test_palavra_longa_legitima_nao_e_laco():
    """Nenhuma letra repetida 8 vezes seguidas - so palavra comprida."""
    assert not VoiceCore._parece_alucinacao("anticonstitucionalissimamente")


@pytest.mark.asyncio
async def test_acordou_e_nao_falou_nada_nao_chega_na_ia():
    """A cena exata do log de 2026-09-19 16:46:39.

        16:46:30  ouvi (rms=0.1486): 'Oi javis!'   -> acordou, respondeu
        16:46:39  ouvi (rms=0.0238): ''            -> ia para a IA assim
        16:46:39  400 Requests ending with a model turn are not supported

    A janela do comando veio vazia e o texto em branco viajava ate o modelo.
    O Gemini descarta a mensagem de usuario vazia, sobra o historico
    terminando na fala DELE, e a API recusa o pedido inteiro - uma chamada de
    cota gasta para nao dizer nada.
    """
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    eventos = []
    for topico in ("voice.wake_detected", "voice.command_received"):
        bus.subscribe(topico, lambda e, t=topico: eventos.append(t))

    processados = []

    async def processar(texto):
        processados.append(texto)
        return "nao deveria responder"

    sintetizador = SintetizadorFalso()
    voice = VoiceCore(
        bus,
        indice_microfone=0,
        # acorda, e a janela do comando vem VAZIA (so silencio no microfone)
        transcritor=TranscritorFalso(["jarvis", ""]),
        sintetizador=sintetizador,
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
    )

    await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    # O que importa: a IA nao foi consultada com a fala em branco.
    assert processados == []
    assert sintetizador.falas == []
    # Acordou (isso e verdade e vale o evento), mas nao houve comando nenhum.
    assert eventos == ["voice.wake_detected"]
    assert voice.estado is EstadoVoz.IDLE

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_acordar_sem_comando_ainda_abre_a_sessao():
    """Quem diz a palavra de ativacao QUER falar - so nao chegou a tempo.

    Barrar a fala vazia nao pode custar a fala seguinte: sem isto, quem
    demorasse meio segundo a mais teria que dizer "Oi jarvis" de novo, que e o
    "falar em dois tempos" que o projeto ja tinha decidido nao exigir.
    """
    bus = EventBus()
    tarefa = await _rodar_bus(bus)

    processados = []

    async def processar(texto):
        processados.append(texto)
        return "sao dez horas"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        # acorda / janela do comando vazia / A PESSOA FALA na janela seguinte
        transcritor=TranscritorFalso(["jarvis", "", "que horas sao"]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
    )

    await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert processados == ["que horas sao"]

    bus.parar()
    await tarefa


def test_portao_devolve_o_motivo_de_cada_descarte():
    """O portao separa OS TRES casos porque eles pesam diferente na sessao:
    alucinacao nao conta silencio, vazio e eco contam."""
    bus = EventBus()
    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso([]),
        sintetizador=SintetizadorFalso(),
        processar_comando=None,
        gravar_audio=_gravar_audio_falso,
    )

    assert voice._motivo_para_descartar("") == "vazio"
    assert voice._motivo_para_descartar("   ") == "vazio"
    assert voice._motivo_para_descartar("FFFFFFFFFFFF") == "alucinacao"
    assert voice._motivo_para_descartar("que horas sao") is None


@pytest.mark.asyncio
async def test_sem_exigir_palavra_a_fala_ja_e_comando():
    """Pedido do dono (2026-10-25): "eu falava e ele ja respondia". Sem
    palavra de ativacao a primeira fala ja vai para a IA, e nada publica
    voice.wake_detected - nao houve ativacao nenhuma."""
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    acordou = []
    bus.subscribe("voice.wake_detected", lambda e: acordou.append(e))
    processados = []

    async def processar(texto):
        processados.append(texto)
        return "sao dez horas"

    sintetizador = SintetizadorFalso()
    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["que horas sao"]),
        sintetizador=sintetizador,
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
        exigir_palavra_ativacao=False,
    )

    await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert processados == ["que horas sao"]
    assert sintetizador.falas == ["sao dez horas"]
    assert acordou == []
    assert voice.estado is EstadoVoz.IDLE

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_sem_exigir_palavra_os_portoes_continuam_barrando():
    """Sem o nome na frente, os portoes sao a unica coisa entre o barulho e a
    cota da IA: transcricao vazia e laco do Whisper nao podem virar chamada."""
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    processados = []

    async def processar(texto):
        processados.append(texto)
        return "nao deveria responder"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["", "obrigado " * 6, ""]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
        exigir_palavra_ativacao=False,
    )

    await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert processados == []

    bus.parar()
    await tarefa


# ---------------------------------------------------------------------------
# Portao de eco POR TEMPO (2026-11-01). Caso real: ele disse "Oi de novo.
# Tudo em ordem por ai?", ouviu "Onde o novo? Tudo em hora em por ai?" - o
# portao por texto nao reconheceu - e, conversando com o eco, mandou as rodas
# andarem. A janela gravada enquanto ele fala tem que virar silencio.
# ---------------------------------------------------------------------------


class SintetizadorQueFalou(SintetizadorFalso):
    def __init__(self, falou: bool) -> None:
        super().__init__()
        self._falou = falou

    def falou_desde(self, instante: float, rastro_s: float) -> bool:
        return self._falou


async def _gravar_com_som(duracao: float):
    import numpy as np

    return np.full(16000, 0.2, dtype="float32")


async def _sessao_sem_palavra(sintetizador) -> list[str]:
    from orion.voice.vad import DetectorAtividadeSonora

    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    processados = []

    async def processar(texto):
        processados.append(texto)
        return "sao dez horas"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["Onde o novo? Tudo em hora em por ai?"]),
        sintetizador=sintetizador,
        processar_comando=processar,
        gravar_audio=_gravar_com_som,
        exigir_palavra_ativacao=False,
        detector_atividade=DetectorAtividadeSonora(),
    )
    await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()
    bus.parar()
    await tarefa
    return processados


@pytest.mark.asyncio
async def test_janela_gravada_enquanto_ele_fala_nao_vira_comando():
    assert await _sessao_sem_palavra(SintetizadorQueFalou(falou=True)) == []


@pytest.mark.asyncio
async def test_janela_sem_fala_dele_continua_virando_comando():
    processados = await _sessao_sem_palavra(SintetizadorQueFalou(falou=False))
    assert processados == ["Onde o novo? Tudo em hora em por ai?"]


def test_falou_desde_conta_a_fala_em_curso_e_o_rastro():
    from types import SimpleNamespace

    from orion.voice.sintese import Sintetizador

    calado = SimpleNamespace(falando=False, fim_ultima_fala=10.0)
    assert Sintetizador.falou_desde(calado, 10.5, 1.0)  # rastro ainda chegando
    assert not Sintetizador.falou_desde(calado, 11.5, 1.0)  # calou antes da janela
    falando = SimpleNamespace(falando=True, fim_ultima_fala=float("-inf"))
    assert Sintetizador.falou_desde(falando, 99.0, 1.0)


# --- 08/11: "Jarvis, me siga" numa respirada so ---


@pytest.mark.asyncio
async def test_comando_logo_depois_da_ativacao_nao_se_perde_e_ele_nao_fala_por_cima():
    """A janela pegou so "Jarvis"; o "me siga" chegou enquanto ele transcrevia.
    Antes ele respondia "Oi? Pode falar!" por cima e descartava a fala."""
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    recebidos = []

    async def processar(texto):
        recebidos.append(texto)
        return "Estou te seguindo."

    continuacoes = []

    async def continuar(duracao):
        continuacoes.append(duracao)
        return object()

    sintetizador = SintetizadorFalso()
    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["jarvis", "me siga"]),
        sintetizador=sintetizador,
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
        continuar_audio=continuar,
        frase_ativacao="Oi? Pode falar!",
    )

    await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert continuacoes, "tinha que ouvir a continuacao antes de responder"
    assert recebidos == ["me siga"]
    assert "Oi? Pode falar!" not in sintetizador.falas
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_so_jarvis_e_silencio_depois_ainda_diz_pode_falar():
    bus = EventBus()
    tarefa = await _rodar_bus(bus)

    async def continuar(duracao):
        return object()

    sintetizador = SintetizadorFalso()
    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["jarvis", "", ""]),
        sintetizador=sintetizador,
        processar_comando=lambda texto: "ok",
        gravar_audio=_gravar_audio_falso,
        continuar_audio=continuar,
        frase_ativacao="Oi? Pode falar!",
    )

    await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert sintetizador.falas[:1] == ["Oi? Pode falar!"]
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_so_jarvis_sem_comando_publica_fim_de_sessao():
    # 14/11: "Jarvis" sem comando nao passava pelo _responder, entao nenhum
    # response_finished saia - e o Atender do Pi segurou o robo parado ate
    # reiniciar. O fim de sessao e o que o solta.
    bus = EventBus()
    tarefa = await _rodar_bus(bus)
    fins = []
    bus.subscribe("voice.session_ended", lambda e: fins.append(e))
    respostas = []
    bus.subscribe("voice.response_finished", lambda e: respostas.append(e))

    async def continuar(duracao):
        return object()

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["jarvis", "", ""]),
        sintetizador=SintetizadorFalso(),
        processar_comando=lambda texto: "ok",
        gravar_audio=_gravar_audio_falso,
        continuar_audio=continuar,
        frase_ativacao="Oi? Pode falar!",
    )

    await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert respostas == []  # o caminho que prendia o robo
    assert len(fins) == 1
    bus.parar()
    await tarefa
