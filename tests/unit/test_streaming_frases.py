"""A resposta sai em frases, para a fala comecar antes do fim da geracao (T5).

Medido em 2026-07-30, mesmo prompt e mesmo modelo:
    hoje (espera tudo, sintetiza tudo) .... 3,50 s ate o 1o som
    streaming ate a 1a frase .............. 1,41 s
    reducao ............................... 60%   (alvo da missao: 40%)

O que estes testes seguram e o que pode regredir em silencio: frase cortada no
meio (o Piper entrega o corte para o ouvido), fala picotada por frase curta
demais, e o robo EMUDECER quando o streaming falha - que seria trocar lentidao
por mudez.
"""
from __future__ import annotations

import asyncio

import pytest

from orion.mission.ai_manager import AiManager


class _Delta:
    def __init__(self, texto):
        self.content = texto


class _Escolha:
    def __init__(self, texto):
        self.delta = _Delta(texto)


class _Pedaco:
    def __init__(self, texto):
        self.choices = [_Escolha(texto)]


def _ia(tmp_path, pedacos=None, erro_apos=None):
    prompt = tmp_path / "p.txt"
    prompt.write_text("voce e o Fofao", encoding="utf-8")
    ia = AiManager(
        modelo="fake",
        caminho_prompt_sistema=str(prompt),
        provider="openai",
        openai_model="fake",
    )
    ia._openai_api_key = "chave-de-teste"

    def _fake(prompt_sistema, texto_usuario):
        for i, p in enumerate(pedacos or []):
            if erro_apos is not None and i == erro_apos:
                raise RuntimeError("a rede caiu no meio")
            yield from ()
            yield p

    return ia


async def _coletar(gerador):
    return [f async for f in gerador]


@pytest.mark.asyncio
async def test_frases_saem_fechadas_e_na_ordem(tmp_path):
    ia = _ia(tmp_path)
    # substitui o produtor sincrono: o teste nao fala com a rede
    ia._openai_em_frases = lambda p, u: iter(
        ["Tudo em ordem por aqui.", "A porta continua fechada."]
    )

    frases = await _coletar(ia.responder_em_frases("tudo bem?"))

    assert frases == ["Tudo em ordem por aqui.", "A porta continua fechada."]


@pytest.mark.asyncio
async def test_sem_openai_devolve_o_texto_inteiro_de_uma_vez(tmp_path):
    """Sem chave, o Ollama local e a reserva (EDR-0021). Responder em uma frase
    so e o comportamento antigo - o que nao pode e ficar sem resposta."""
    ia = _ia(tmp_path)
    ia._openai_api_key = None

    async def _responder(texto, contexto=None):
        return "resposta inteira da reserva local"

    ia.responder = _responder

    frases = await _coletar(ia.responder_em_frases("oi"))

    assert frases == ["resposta inteira da reserva local"]


@pytest.mark.asyncio
async def test_falha_antes_da_primeira_frase_cai_para_a_reserva(tmp_path):
    """REGRESSAO: trocar lentidao por MUDEZ seria pior que o problema original.
    Se o streaming morre antes de qualquer frase, ainda da tempo de usar o
    caminho local."""
    ia = _ia(tmp_path)

    def _explode(p, u):
        raise RuntimeError("a rede caiu antes de qualquer token")
        yield  # noqa: unreachable - marca a funcao como geradora

    ia._openai_em_frases = _explode
    ia._chamar_ollama = lambda p, u: "reserva local respondeu"

    frases = await _coletar(ia.responder_em_frases("oi"))

    assert frases == ["reserva local respondeu"]


@pytest.mark.asyncio
async def test_falha_no_meio_nao_repete_o_que_ja_foi_dito(tmp_path):
    """Se ja falamos a primeira frase, cair na reserva faria o robo dizer tudo
    de novo desde o comeco - em voz alta, por cima do que ele acabou de falar."""
    ia = _ia(tmp_path)

    def _meio(p, u):
        yield "Primeira frase ja dita."
        raise RuntimeError("caiu depois da primeira")

    ia._openai_em_frases = _meio
    ia._chamar_ollama = lambda p, u: "NAO DEVE APARECER"

    frases = await _coletar(ia.responder_em_frases("oi"))

    assert frases == ["Primeira frase ja dita."]
    assert "NAO DEVE APARECER" not in frases


def test_frase_curta_demais_nao_vira_trecho_solto():
    """"Sim." sozinho custaria uma carga do Piper para meio segundo de audio, e
    a fala sairia picotada. O limiar existe para juntar com a seguinte."""
    assert AiManager.MINIMO_CARACTERES_FRASE > len("Sim.")


def test_corta_so_em_fim_de_frase():
    """O Piper tira a prosodia inteira da pontuacao: cortar aqui e cortar onde
    ele mesmo faria a pausa. Cortar por numero de caracteres entregaria o meio
    da frase para o ouvido."""
    for sinal in (".", "!", "?"):
        assert sinal in AiManager.FIM_DE_FRASE
