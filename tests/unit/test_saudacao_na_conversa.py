"""Testes de quando o cumprimento NAO pode sair (2026-08-09, noite).

Queixa do dono, ao vivo:

> *"no meio de uma caonversa ele me sauda e diz q é um prazer me ver sendo q
> ja estou falando com ele algum tempo"*

O `_conversando` que ja existia so valia DENTRO do turno (de "Fofao" ate a
resposta terminar). Entre um turno e outro a pessoa voltava a ser alguem que
acabou de aparecer na frente da camera, e ouvia "que bom te ver" no meio do
papo.
"""
import pytest

from orion.kernel.event_bus import EventBus, Evento
from orion.mission.saudacao import TOPICO_RECONHECIDA, SaudacaoEspontanea


class _Relogio:
    """Tempo na mao: estes testes sao sobre janelas de tempo."""

    def __init__(self) -> None:
        self.agora = 1000.0

    def __call__(self) -> float:
        return self.agora

    def avancar(self, segundos: float) -> None:
        self.agora += segundos


@pytest.fixture
def cenario():
    bus = EventBus()
    ditas: list[str] = []

    async def falar(frase: str) -> None:
        ditas.append(frase)

    relogio = _Relogio()
    saudacao = SaudacaoEspontanea(
        bus, falar, agora=relogio, familia=[{"nome": "João Paulo"}]
    )
    return bus, ditas, relogio, saudacao


async def _ver_pessoa(bus, saudacao):
    """Entrega o evento direto ao handler, sem depender do laco do bus."""
    await saudacao._ao_reconhecer(Evento(TOPICO_RECONHECIDA, {"nome": "João Paulo"}))
    if saudacao._tarefa_cumprimento is not None:
        await saudacao._tarefa_cumprimento


@pytest.mark.asyncio
async def test_nao_cumprimenta_entre_dois_turnos_da_mesma_conversa(cenario):
    """O caso do dono: ele fala, o robo responde, e a camera o "descobre"."""
    bus, ditas, relogio, saudacao = cenario

    await saudacao._ao_acordar(None)
    await saudacao._ao_terminar_resposta(None)

    # Dois minutos depois, entre um turno e outro, a visao o reconhece.
    relogio.avancar(120)
    await _ver_pessoa(bus, saudacao)

    assert ditas == [], f"cumprimentou no meio da conversa: {ditas}"


@pytest.mark.asyncio
async def test_nao_cumprimenta_durante_a_propria_resposta(cenario):
    """No meio da própria fala não cumprimenta - adia para o fim do turno.

    A chegada não se perde: quem aparece enquanto o robô fala é cumprimentado
    assim que a resposta termina (dono, 19/08).
    """
    bus, ditas, relogio, saudacao = cenario
    await saudacao._ao_acordar(None)
    await saudacao._ao_comecar_resposta(None)  # o robô está falando
    await _ver_pessoa(bus, saudacao)
    assert ditas == []  # ainda falando: não fala por cima

    await saudacao._ao_terminar_resposta(None)
    if saudacao._tarefa_cumprimento is not None:
        await saudacao._tarefa_cumprimento
    assert len(ditas) == 1
    assert "João Paulo" in ditas[0]


@pytest.mark.asyncio
async def test_acordou_sem_resposta_e_chegada_cumprimenta(cenario):
    """19/08: a visão reconheceu o dono 6 s depois de ele dizer "Fofão".

    O wake abria a janela de conversa na hora e a saudação morria antes de
    nascer - o robô via o dono chegar e não dizia nada. Wake sozinho não é
    conversa: quem aparece enquanto o robô acorda está CHEGANDO.
    """
    bus, ditas, relogio, saudacao = cenario
    await saudacao._ao_acordar(None)  # "Fofão"
    await _ver_pessoa(bus, saudacao)  # a visão reconhece logo depois
    assert len(ditas) == 1
    assert "João Paulo" in ditas[0]


@pytest.mark.asyncio
async def test_cumprimenta_quem_chega_de_verdade(cenario):
    """Sem conversa nenhuma antes, ver alguem E uma chegada."""
    bus, ditas, relogio, saudacao = cenario
    await _ver_pessoa(bus, saudacao)
    assert len(ditas) == 1
    assert "João Paulo" in ditas[0]


@pytest.mark.asyncio
async def test_depois_da_janela_a_chegada_volta_a_valer(cenario):
    """Conversou, foi embora, voltou meia hora depois: isso E chegar."""
    bus, ditas, relogio, saudacao = cenario
    await saudacao._ao_acordar(None)
    await saudacao._ao_terminar_resposta(None)

    relogio.avancar(1000)  # passou da janela de conversa (900 s)
    await _ver_pessoa(bus, saudacao)

    assert len(ditas) == 1


@pytest.mark.asyncio
async def test_conversa_nao_adia_o_cumprimento_seguinte(cenario):
    """Ver a pessoa durante a conversa nao pode gastar a vez dela.

    Se o handler marcasse `_cumprimentados` ao recusar, cada quadro visto
    durante o papo empurraria o proximo cumprimento por meia hora.
    """
    bus, ditas, relogio, saudacao = cenario
    await saudacao._ao_acordar(None)
    await saudacao._ao_terminar_resposta(None)

    for _ in range(5):  # a camera o ve varias vezes durante a conversa
        relogio.avancar(10)
        await _ver_pessoa(bus, saudacao)
    assert ditas == []

    relogio.avancar(1000)  # a conversa acabou de verdade
    await _ver_pessoa(bus, saudacao)
    assert len(ditas) == 1


@pytest.mark.asyncio
async def test_marcador_de_acao_nao_vai_para_o_alto_falante():
    """Em 09/08 o robo FALOU "#acao:girar_cabeca Olá, criador!".

    O compositor usa o `ai_manager`, cujo prompt carrega o catalogo de acoes -
    entao o modelo as vezes abre o cumprimento com o marcador. `girar_cabeca`
    nem existe no catalogo, e mesmo assim saiu na voz.
    """
    bus = EventBus()
    ditas: list[str] = []

    async def falar(frase: str) -> None:
        ditas.append(frase)

    async def compor(nome, descricao, contexto, evitar):
        return "#acao:girar_cabeca Olá, criador! Bom te ver de novo por aqui."

    saudacao = SaudacaoEspontanea(bus, falar, compor=compor, familia=[{"nome": "João Paulo"}])
    await _ver_pessoa(bus, saudacao)

    assert ditas == ["Olá, criador! Bom te ver de novo por aqui."]
    assert "#acao" not in ditas[0]


@pytest.mark.asyncio
async def test_marcador_sozinho_cai_na_frase_de_reserva():
    """So o marcador e nada depois: melhor a frase comum que o silencio."""
    bus = EventBus()
    ditas: list[str] = []

    async def falar(frase: str) -> None:
        ditas.append(frase)

    async def compor(nome, descricao, contexto, evitar):
        return "#acao:olhar_frente"

    saudacao = SaudacaoEspontanea(bus, falar, compor=compor, familia=[{"nome": "João Paulo"}])
    await _ver_pessoa(bus, saudacao)

    assert len(ditas) == 1
    assert "João Paulo" in ditas[0]
    assert "#acao" not in ditas[0]
