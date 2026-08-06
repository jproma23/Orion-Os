"""Testes do portão anti-invenção do transcritor (Cap 9).

Nasceu em 2026-07-29 de uma reclamação do dono, medida no mesmo dia:
*"quando o whisper não entende ele inventa, isso é ruim"*. Sobre ruído sem
voz, o Whisper devolveu 'O que é isso? O que é isso?' - frase que está no
banco desde 25/07. Ele repetiu o passado em vez de calar.

Aqui não se testa o Whisper (é modelo, não é nosso). Testa-se o PORTÃO: que
os freios chegam à lib, e que segmento marcado como pouco confiável é
descartado em vez de virar frase.
"""
from __future__ import annotations

import pytest

# Mesmo motivo de test_ouvidos_esp32.py: transcricao.py importa numpy e
# faster_whisper, que so existem no Notebook. Sem o guarda, a coleta explode
# e derruba a suite inteira do Pi.
pytest.importorskip('numpy')
pytest.importorskip('faster_whisper')

import numpy as np  # noqa: E402

from orion.voice.transcricao import Transcritor, razao_de_compressao  # noqa: E402

MUDO = np.zeros(1600, dtype="float32")


class _Segmento:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=0.0):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


class _ModeloFalso:
    """Imita o WhisperModel: guarda os parâmetros recebidos e devolve o que o
    teste mandar."""

    def __init__(self, segmentos):
        self.segmentos = segmentos
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return iter(self.segmentos), None


def _montar(segmentos, **kw):
    # __new__ para não baixar modelo de verdade: o que se testa é o portão.
    t = Transcritor.__new__(Transcritor)
    falso = _ModeloFalso(segmentos)
    t._modelo = falso
    t._idioma = "pt"
    t._vad_filter = kw.get("vad_filter", True)
    t._no_speech_threshold = kw.get("no_speech_threshold", 0.6)
    t._log_prob_threshold = kw.get("log_prob_threshold", -1.0)
    t._compression_ratio_threshold = kw.get("compression_ratio_threshold", 2.4)
    t._hotwords = kw.get("hotwords", "Fofao")
    return t, falso


@pytest.mark.asyncio
async def test_os_freios_chegam_a_lib():
    """Até 2026-07-29 a chamada era `transcribe(audio, language="pt")` e nada
    mais - todos os freios que a lib oferece estavam desligados."""
    t, falso = _montar([_Segmento("oi")])
    await t.transcrever(MUDO)

    k = falso.kwargs
    assert k["vad_filter"] is True, "o VAD é o freio mais eficaz"
    assert k["condition_on_previous_text"] is False, (
        "é ESTE default que fazia o modelo repetir frases antigas do banco"
    )
    assert k["temperature"] == 0, "sem amostragem criativa: silêncio não vira poesia"
    assert k["no_speech_threshold"] == 0.6
    assert k["log_prob_threshold"] == -1.0
    assert k["compression_ratio_threshold"] == 2.4
    assert k["hotwords"] == "Fofao"


@pytest.mark.asyncio
async def test_segmento_marcado_como_NAO_fala_e_descartado():
    """O caso exato do dono: o modelo devolve texto E admite que
    provavelmente não era fala. Antes o texto passava."""
    t, _ = _montar([_Segmento("O que é isso? O que é isso?", no_speech_prob=0.95)])
    assert await t.transcrever(MUDO) == ""


@pytest.mark.asyncio
async def test_segmento_sem_confianca_e_descartado():
    t, _ = _montar([_Segmento("mecânia de indígena", avg_logprob=-2.5)])
    assert await t.transcrever(MUDO) == ""


@pytest.mark.asyncio
async def test_fala_boa_passa_inteira():
    """O portão não pode calar quem falou de verdade - seria trocar um
    problema por outro pior."""
    t, _ = _montar([
        _Segmento("Fofão,", no_speech_prob=0.02, avg_logprob=-0.3),
        _Segmento("acende a luz", no_speech_prob=0.05, avg_logprob=-0.4),
    ])
    assert await t.transcrever(MUDO) == "Fofão, acende a luz"


@pytest.mark.asyncio
async def test_mistura_mantem_so_o_confiavel():
    t, _ = _montar([
        _Segmento("acende a luz", no_speech_prob=0.03, avg_logprob=-0.4),
        _Segmento("E aí? E aí? E aí?", no_speech_prob=0.9, avg_logprob=-0.2),
    ])
    assert await t.transcrever(MUDO) == "acende a luz"


@pytest.mark.asyncio
async def test_vazio_quando_nao_ha_segmento():
    """String vazia é resposta legítima: o VoiceCore já a trata como "não
    entendi". É exatamente o que o dono pediu no lugar da invenção."""
    t, _ = _montar([])
    assert await t.transcrever(MUDO) == ""


# ---------------------------------------------------------------------------
# Portao de REPETICAO (2026-08-05).
#
# O `compression_ratio_threshold` ja era passado para a lib desde 29/07, e nao
# rejeitava nada: la dentro ele so decide "tenta de novo com temperatura
# maior", e com `temperature=0` nao ha segunda tentativa - o texto repetitivo
# fica. Estes testes travam a rejeicao do lado de ca, onde ela e de verdade.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_laco_confiante_e_descartado():
    """O caso que passava: laco com no_speech BAIXA e logprob ALTA - o modelo
    convicto do lixo. Nenhum dos dois portoes antigos o via."""
    t, _ = _montar([_Segmento("O que é " * 60, no_speech_prob=0.02, avg_logprob=-0.2)])
    assert await t.transcrever(MUDO) == ""


@pytest.mark.asyncio
async def test_token_travado_e_descartado():
    """220 letras F, real no log de 05/08."""
    t, _ = _montar([_Segmento("F" * 220, no_speech_prob=0.05, avg_logprob=-0.3)])
    assert await t.transcrever(MUDO) == ""


@pytest.mark.asyncio
async def test_frase_normal_nao_e_confundida_com_repeticao():
    """O risco do portao novo e calar quem falou. Frase curta comprime mal
    (o cabecalho do zlib pesa), entao fica bem abaixo do limiar."""
    t, _ = _montar([
        _Segmento("Fofão, vai lá no quarto ver se o Kamal está", no_speech_prob=0.03,
                  avg_logprob=-0.4),
    ])
    assert await t.transcrever(MUDO) == "Fofão, vai lá no quarto ver se o Kamal está"


@pytest.mark.asyncio
async def test_laco_no_meio_da_janela_nao_leva_a_fala_boa_junto():
    """Segmento a segmento: o que repetiu sai, o que a pessoa falou fica."""
    t, _ = _montar([
        _Segmento("acende a luz", no_speech_prob=0.03, avg_logprob=-0.4),
        _Segmento("o que é " * 40, no_speech_prob=0.04, avg_logprob=-0.25),
    ])
    assert await t.transcrever(MUDO) == "acende a luz"


def test_razao_de_compressao_separa_os_dois_casos():
    """A medida em si, sem o transcritor no meio - para o limiar 2.4 poder ser
    revisto depois com base em numero, nao em impressao."""
    assert razao_de_compressao("O que é " * 60) > 2.4
    assert razao_de_compressao("Fofão, acende a luz da sala") < 2.4
