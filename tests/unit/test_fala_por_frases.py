"""O VoiceCore fala conforme as frases chegam, e descarta ao ser interrompido.

T5.a, 2026-07-30. O ganho medido (3,33 s -> 1,84 s ate o primeiro som) so vale
se a fala comecar de verdade antes do fim da geracao - e o risco que vem junto
e o do cliente do ESP32: fila que nao se descarta entrega som do passado.
"""
from __future__ import annotations

import asyncio

import pytest

# O Motion Core (Pi) nao instala o extra `mission`, entao `numpy` e companhia
# nao existem la - e `voice_core` os importa em cadeia. Sem estas guardas a
# COLETA do pytest quebra e leva a suite INTEIRA junto: em 2026-08-03 o Pi
# rodava 11 testes em vez de 538 por causa desta linha faltando. Mesmo idioma
# de test_voice_core.py e test_vision_core.py.
pytest.importorskip("numpy")
pytest.importorskip("sounddevice")

from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.voice.voice_core import VoiceCore  # noqa: E402


class SintetizadorEspiao:
    def __init__(self):
        self.ditas: list[str] = []

    async def falar(self, texto: str) -> None:
        self.ditas.append(texto)


def _voz(bus, frases, sintetizador, atraso=0.0):
    async def em_frases(texto):
        for f in frases:
            if atraso:
                await asyncio.sleep(atraso)
            yield f

    async def inteiro(texto):
        return " ".join(frases)

    return VoiceCore(
        event_bus=bus,
        indice_microfone=0,
        transcritor=None,
        sintetizador=sintetizador,
        processar_comando=inteiro,
        processar_comando_em_frases=em_frases,
    )


@pytest.mark.asyncio
async def test_fala_cada_frase_na_ordem_em_que_chega():
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    sint = SintetizadorEspiao()
    voz = _voz(bus, ["Tudo em ordem.", "A porta esta fechada."], sint)

    await voz._responder_falando_por_frases("tudo bem?")

    assert sint.ditas == ["Tudo em ordem.", "A porta esta fechada."]
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_response_started_sai_com_a_primeira_frase_nao_com_o_texto_todo():
    """No instante do response_started o texto inteiro AINDA NAO EXISTE. Quem
    consome o evento e a legenda do avatar: ela passa a acompanhar a fala."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    comecos, fins = [], []
    bus.subscribe("voice.response_started", lambda e: comecos.append(e.dados["texto"]))
    bus.subscribe("voice.response_finished", lambda e: fins.append(e.dados["texto"]))
    sint = SintetizadorEspiao()
    voz = _voz(bus, ["Primeira.", "Segunda frase aqui."], sint)

    await voz._responder_falando_por_frases("oi")
    await bus.aguardar_fila_vazia()

    assert comecos == ["Primeira."]
    assert fins == ["Primeira. Segunda frase aqui."]
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_interromper_descarta_o_que_ainda_nao_foi_dito():
    """REGRESSAO do padrao do ESP32: sem descarte, as frases ja geradas seguem
    saindo pela caixa depois de a conversa ter virado outra coisa - o robo
    responde ao passado."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    sint = SintetizadorEspiao()
    voz = _voz(bus, ["Uma.", "Duas.", "Tres.", "Quatro."], sint, atraso=0.02)

    async def interromper():
        await asyncio.sleep(0.03)
        voz.interromper_fala()

    await asyncio.gather(voz._responder_falando_por_frases("oi"), interromper())

    assert len(sint.ditas) < 4, f"nada foi descartado: falou {sint.ditas}"
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_falha_antes_de_qualquer_frase_ainda_diz_alguma_coisa():
    """Ficar MUDO seria pior que o problema que T5 veio resolver."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    sint = SintetizadorEspiao()

    async def explode(texto):
        raise RuntimeError("caiu antes de gerar")
        yield  # noqa - marca como geradora

    voz = VoiceCore(
        event_bus=bus,
        indice_microfone=0,
        transcritor=None,
        sintetizador=sint,
        processar_comando=None,
        processar_comando_em_frases=explode,
    )

    await voz._responder_falando_por_frases("oi")

    assert sint.ditas and "Desculpa" in sint.ditas[0]
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_falha_no_meio_nao_emenda_desculpa_no_meio_da_resposta():
    """Se ja dissemos algo que fazia sentido, emendar "desculpa, tive um
    problema" em cima soa pior que simplesmente parar."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    sint = SintetizadorEspiao()

    async def meio(texto):
        yield "Tudo em ordem por aqui."
        raise RuntimeError("caiu depois da primeira")

    voz = VoiceCore(
        event_bus=bus,
        indice_microfone=0,
        transcritor=None,
        sintetizador=sint,
        processar_comando=None,
        processar_comando_em_frases=meio,
    )

    await voz._responder_falando_por_frases("oi")

    assert sint.ditas == ["Tudo em ordem por aqui."]
    assert not any("Desculpa" in d for d in sint.ditas)
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_sem_o_caminho_de_frases_o_comportamento_e_o_antigo():
    """Compatibilidade: quem nao injeta o callback novo continua falando de
    uma vez so, como antes de 2026-07-30."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    sint = SintetizadorEspiao()

    async def inteiro(texto):
        return "resposta inteira de uma vez"

    voz = VoiceCore(
        event_bus=bus,
        indice_microfone=0,
        transcritor=None,
        sintetizador=sint,
        processar_comando=inteiro,
    )

    assert voz._processar_comando_em_frases is None
    bus.parar()
    await tarefa
