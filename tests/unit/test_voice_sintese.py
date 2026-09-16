"""Testes da reamostragem de audio da sintese de voz (Cap 9 s.3, 8-9).

So a logica pura de reamostragem - sem instanciar Piper/sounddevice de
verdade (precisaria do modelo baixado e de hardware). Pula onde numpy nao
esta instalado (ex.: neste Raspberry Pi).
"""
import pytest

pytest.importorskip("numpy")
pytest.importorskip("sounddevice")
pytest.importorskip("piper")

import numpy as np  # noqa: E402

from orion.voice.sintese import _reamostrar  # noqa: E402


def test_mesma_taxa_retorna_array_identico():
    audio = np.array([1, 2, 3, 4], dtype=np.int16)
    resultado = _reamostrar(audio, 22050, 22050)
    assert np.array_equal(resultado, audio)


def test_upsample_aumenta_quantidade_de_amostras():
    audio = np.linspace(0, 1000, 100).astype(np.int16)
    resultado = _reamostrar(audio, 22050, 44100)
    assert len(resultado) == 200


def test_downsample_diminui_quantidade_de_amostras():
    audio = np.linspace(0, 1000, 200).astype(np.int16)
    resultado = _reamostrar(audio, 44100, 22050)
    assert len(resultado) == 100


def test_preserva_dtype():
    audio = np.array([100, 200, 300], dtype=np.int16)
    resultado = _reamostrar(audio, 22050, 44100)
    assert resultado.dtype == np.int16


# ---------------------------------------------------------------------------
# DUAS FALAS AO MESMO TEMPO MATAVAM O PROCESSO (diagnosticado em 2026-08-05)
#
# 10 quedas em 7 dias, nenhuma com excecao no log: o servico era morto com
# SIGSEGV e o systemd o reerguia ~45 s depois. Do lado de fora, "o Fofao
# desconectou sozinho". Nas 4 examinadas, a ultima linha e sempre uma saudacao
# espontanea encostada numa resposta da conversa.
#
# Sao dois caminhos independentes ate o MESMO sintetizador - `voice_core.falar`
# (conversa, disparada em create_task) e `sintetizador.falar` direto (saudacao
# e entrega de recado) - e `falar` joga o trabalho num `asyncio.to_thread`.
# Duas threads de verdade em cima do PiperVoice (objeto C++ unico) e do stream
# global do sounddevice: a segunda `sd.play` derruba o stream enquanto a
# primeira ainda esta no `sd.wait`, e o processo morre.
#
# Custou o primeiro recado entregue da historia do projeto: o robo falou,
# morreu no meio, e como `marcar_entregue` so roda depois da fala terminar, o
# recado voltou para a fila com a pessoa na frente ouvindo.
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402

from orion.voice.sintese import Sintetizador  # noqa: E402


def _sintetizador_sem_hardware() -> Sintetizador:
    """Um Sintetizador sem Piper carregado nem placa de som aberta.

    `__init__` faz `PiperVoice.load()`, que exige o modelo em disco. Aqui so
    interessa a serializacao, entao monta-se o objeto com o unico atributo que
    ela usa - mesmo espirito do resto deste arquivo, que testa a reamostragem
    sem instanciar nada.
    """
    sintetizador = Sintetizador.__new__(Sintetizador)
    sintetizador._trava_fala = asyncio.Lock()
    # sem saida pela tela: estes testes exercitam o caminho do sounddevice
    # (ver orion.display.ponte_serial para o outro caminho)
    sintetizador._tocar_na_tela = None
    return sintetizador


@pytest.mark.asyncio
async def test_duas_falas_simultaneas_nao_se_sobrepoem(monkeypatch):
    dentro = 0
    pico = 0
    ordem = []

    async def _to_thread_falso(funcao, *args, **kwargs):
        nonlocal dentro, pico
        dentro += 1
        pico = max(pico, dentro)
        await asyncio.sleep(0.02)  # o tempo em que a fala esta tocando
        ordem.append("fim")
        dentro -= 1

    monkeypatch.setattr("orion.voice.sintese.asyncio.to_thread", _to_thread_falso)
    sintetizador = _sintetizador_sem_hardware()

    await asyncio.gather(
        sintetizador.falar("oi, Joao Paulo"),
        sintetizador.falar("a Marah pediu para voce ligar para a escola"),
    )

    assert pico == 1, f"{pico} falas ao mesmo tempo - e o segfault que mata o robo"
    assert len(ordem) == 2, "a trava engoliu uma das falas em vez de atrasa-la"


@pytest.mark.asyncio
async def test_falha_numa_fala_nao_tranca_a_proxima(monkeypatch):
    """A trava nao pode virar um cadeado: se a saida de audio falhar uma vez,
    o robo tem que continuar falando. Emudecer por causa de um erro
    transitorio e pior do que o problema original."""
    chamadas = []

    async def _to_thread_falso(funcao, *args, **kwargs):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise RuntimeError("PortAudio caiu")

    monkeypatch.setattr("orion.voice.sintese.asyncio.to_thread", _to_thread_falso)
    sintetizador = _sintetizador_sem_hardware()

    with pytest.raises(RuntimeError):
        await sintetizador.falar("primeira")

    await sintetizador.falar("segunda")  # nao pode travar aqui
    assert len(chamadas) == 2
