"""Identificacao de locutor por voz - quem falou, so pelo microfone.

Espelha a regra do reconhecimento facial: atribuir errado e PIOR que nao
atribuir. O que estes testes protegem e (1) achar a pessoa certa quando a
voz casa, (2) NUNCA inventar um nome abaixo do limiar, e (3) o VoiceCore
publicar `voice.person_recognized` para o `quem_fala` creditar - a ponte
que fecha o buraco da conversa por voz sem rosto.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest

pytest.importorskip("torch")
pytest.importorskip("numpy")

import torch  # noqa: E402

from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.voice.identificador_locutor import (  # noqa: E402
    IdentificadorLocutor,
    LocutorIdentificado,
)
from orion.voice.voice_core import VoiceCore  # noqa: E402
from orion.voice.wake_word import DetectorPalavraAtivacao  # noqa: E402


def _vetor(sinal: float) -> np.ndarray:
    """Vetor de 192 dims apontando para o quadrante do sinal: +A, -A, +B..."""
    base = np.full(192, 0.1 * sinal, dtype=np.float32)
    return base / (np.linalg.norm(base) + 1e-9)


class ClassificadorFalso:
    """Devolve o embedding do 'falante' pelo sinal da media do audio: media
    positiva = falante A, negativa = falante B. Deterministico, sem ECAPA."""

    def encode_batch(self, wav, wav_lens=None):
        sinal = 1.0 if float(wav.mean()) >= 0.0 else -1.0
        t = torch.from_numpy(_vetor(sinal)).unsqueeze(0).unsqueeze(0)
        return t


def _identificador(*, limiar=0.5, pessoas):
    ident = IdentificadorLocutor(
        limiar_cosseno=limiar, carregar_modelo=ClassificadorFalso
    )
    ident.carregar_vozes_conhecidas(pessoas)
    return ident


def _pessoa(nome, embedding: np.ndarray | None) -> dict:
    registro = {"id": hash(nome) % 1000, "nome": nome}
    if embedding is not None:
        registro["embedding_voz"] = embedding.tobytes()
    return registro


def _audio(media_positiva: bool) -> np.ndarray:
    return np.full(16000, 0.5 if media_positiva else -0.5, dtype=np.float32)


@pytest.mark.asyncio
async def test_carrega_so_quem_tem_voiceprint():
    ident = IdentificadorLocutor(carregar_modelo=ClassificadorFalso)
    ident.carregar_vozes_conhecidas(
        [
            _pessoa("Joao Paulo", _vetor(1.0)),
            _pessoa("Marah", None),  # sem voz cadastrada - ignora
        ]
    )
    assert len(ident._vozes) == 1


@pytest.mark.asyncio
async def test_identifica_pela_voz_certa():
    ident = _identificador(
        pessoas=[_pessoa("Joao Paulo", _vetor(1.0)), _pessoa("Marah", _vetor(-1.0))]
    )
    achado = await ident.identificar(_audio(media_positiva=True))
    assert achado is not None
    assert achado.nome == "Joao Paulo"
    assert achado.confianca == pytest.approx(1.0, abs=1e-3)


@pytest.mark.asyncio
async def test_voz_oposta_nao_vira_chute():
    ident = _identificador(pessoas=[_pessoa("Joao Paulo", _vetor(1.0))])
    achado = await ident.identificar(_audio(media_positiva=False))
    assert achado is None, "cosseno ~0 e o da MESMA pessoa - tem que ser None"


@pytest.mark.asyncio
async def test_limiar_alto_recusa_a_mesma_voz():
    ident = _identificador(limiar=0.9, pessoas=[_pessoa("A", _vetor(1.0))])
    achado = await ident.identificar(_audio(media_positiva=False))
    assert achado is None


@pytest.mark.asyncio
async def test_sem_voiceprint_nenhum_nao_consulta_nada():
    ident = IdentificadorLocutor(carregar_modelo=ClassificadorFalso)
    achado = await ident.identificar(_audio(media_positiva=True))
    assert achado is None


@pytest.mark.asyncio
async def test_voz_do_outro_troca_o_credito():
    ident = _identificador(
        pessoas=[_pessoa("Joao Paulo", _vetor(1.0)), _pessoa("Marah", _vetor(-1.0))]
    )
    assert (await ident.identificar(_audio(media_positiva=False))).nome == "Marah"


class TranscritorFalso:
    def __init__(self, textos: list[str]) -> None:
        self._textos = list(textos)

    async def transcrever(self, audio) -> str:
        return self._textos.pop(0) if self._textos else ""


class SintetizadorFalso:
    async def falar(self, texto: str) -> None:
        return None


async def _gravar_audio_falso(duracao: float):
    return object()


class IdentificadorFalso:
    def __init__(self, resultado: LocutorIdentificado | None) -> None:
        self._resultado = resultado
        self.chamadas = 0

    async def identificar(self, audio) -> LocutorIdentificado | None:
        self.chamadas += 1
        return self._resultado


@pytest.mark.asyncio
async def test_voice_core_publica_quando_identifica():
    """Comando em janela propria: identificacao corre junto da transcricao e
    `voice.person_recognized` sai antes de responder."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    recebidos = []
    bus.subscribe("voice.person_recognized", lambda e: recebidos.append(e.dados))

    async def processar(texto):
        return "ok"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["jarvis", "que horas sao"]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
        identificador_locutor=IdentificadorFalso(
            LocutorIdentificado(pessoa_id=1, nome="Joao Paulo", confianca=0.9)
        ),
    )

    processou = await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert processou is True
    # Identifica no comando E em cada fala da sessao: o primeiro evento ja
    # carrega o nome antes de qualquer resposta.
    assert recebidos, "deveria ter publicado voice.person_recognized"
    assert recebidos[0] == {"nome": "Joao Paulo", "confianca": 0.9}

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_voice_core_nao_publica_quando_nao_identifica():
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    recebidos = []
    bus.subscribe("voice.person_recognized", lambda e: recebidos.append(e.dados))

    async def processar(texto):
        return "ok"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["jarvis", "que horas sao"]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
        identificador_locutor=IdentificadorFalso(None),
    )

    await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert recebidos == []

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_voice_core_identifica_comando_na_propria_janela():
    """'Jarvis, que horas sao' numa respirada: a identificacao usa a janela
    da ativacao (o comando inteiro esta nela)."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    recebidos = []
    bus.subscribe("voice.person_recognized", lambda e: recebidos.append(e.dados))

    async def processar(texto):
        return "ok"

    identificador = IdentificadorFalso(
        LocutorIdentificado(pessoa_id=1, nome="Joao Paulo", confianca=0.8)
    )
    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["jarvis que horas sao"]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
        identificador_locutor=identificador,
    )

    await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert identificador.chamadas >= 1, "a janela da ativacao deve ser identificada"
    assert recebidos and recebidos[0] == {"nome": "Joao Paulo", "confianca": 0.8}

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_identificador_desligado_nao_publica():
    """VoiceCore sem identificador (comportamento de sempre) nao publica."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    recebidos = []
    bus.subscribe("voice.person_recognized", lambda e: recebidos.append(e.dados))

    async def processar(texto):
        return "ok"

    voice = VoiceCore(
        bus,
        indice_microfone=0,
        transcritor=TranscritorFalso(["jarvis", "que horas sao"]),
        sintetizador=SintetizadorFalso(),
        processar_comando=processar,
        gravar_audio=_gravar_audio_falso,
    )

    await voice.ciclo_uma_vez()
    await bus.aguardar_fila_vazia()

    assert recebidos == []

    bus.parar()
    await tarefa
