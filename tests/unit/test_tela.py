"""Testes do descanso de tela (Cap 13 s.3).

O que estes testes protegem, em uma frase: a tela ACENDE por pedido e por
quadro, NAO acende por evento comum, e apaga sozinha depois do prazo.

O relogio e o `xset` sao injetados justamente para isto: provar "apaga
depois de 3 minutos" com o relogio de verdade custaria 3 minutos de suite,
e a suite roda em duas maquinas.
"""
import asyncio

import pytest

from orion.display.tela import ControleTela
from orion.kernel.event_bus import EventBus, Evento


class XsetFalso:
    """Guarda o que foi pedido ao xset, e responde que deu certo."""

    def __init__(self, funciona: bool = True) -> None:
        self.chamadas: list[tuple[str, ...]] = []
        self._funciona = funciona

    async def __call__(self, argumentos: tuple[str, ...]) -> bool:
        self.chamadas.append(argumentos)
        return self._funciona


class RelogioFalso:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def avancar(self, segundos: float) -> None:
        self.t += segundos


def _controle(minutos: float = 3.0) -> tuple[ControleTela, XsetFalso, RelogioFalso]:
    xset, relogio = XsetFalso(), RelogioFalso()
    controle = ControleTela(minutos, executar=xset, agora=relogio, habilitado=True)
    # `habilitado=True` no construtor ainda pode cair para False se a maquina
    # nao tiver xset - e no Pi ela nao tem. Como o executor e falso, o teste
    # forca o modulo a se considerar ativo.
    controle._habilitado = True
    return controle, xset, relogio


@pytest.mark.asyncio
async def test_acordar_acende_a_tela():
    controle, xset, _ = _controle()
    await controle.acordar("teste")
    assert controle.acesa
    assert ("dpms", "force", "on") in xset.chamadas


@pytest.mark.asyncio
async def test_acordar_duas_vezes_nao_repete_o_xset():
    controle, xset, _ = _controle()
    await controle.acordar("teste")
    await controle.acordar("teste")
    assert xset.chamadas.count(("dpms", "force", "on")) == 1


@pytest.mark.asyncio
async def test_adiar_nao_acende_tela_apagada():
    """A regra central: evento comum NAO acende a tela.

    Sem isto, voz e visao (que acontecem o dia todo) manteriam a tela ligada
    para sempre por um caminho lateral - era o erro do desenho anterior.
    """
    controle, xset, _ = _controle()
    await controle.adiar("voice.status")
    assert not controle.acesa
    assert xset.chamadas == []


@pytest.mark.asyncio
async def test_apaga_quando_o_prazo_vence():
    controle, xset, relogio = _controle(minutos=3.0)
    await controle.acordar("teste")
    relogio.avancar(179.0)
    await controle.conferir_prazo()
    assert controle.acesa, "apagou antes dos 3 minutos"
    relogio.avancar(2.0)
    await controle.conferir_prazo()
    assert not controle.acesa
    assert ("dpms", "force", "off") in xset.chamadas


@pytest.mark.asyncio
async def test_adiar_segura_a_tela_acesa_por_mais_um_prazo():
    """A aula longa: o robo explica dois minutos sem escrever no quadro."""
    controle, _, relogio = _controle(minutos=3.0)
    await controle.acordar("display.quadro")
    relogio.avancar(170.0)
    await controle.adiar("display.aula")
    relogio.avancar(170.0)
    await controle.conferir_prazo()
    assert controle.acesa, "a tela apagou no meio da aula"


@pytest.mark.asyncio
async def test_o_ciclo_de_escuta_nao_segura_a_tela_acesa():
    """Regressao do que foi MEDIDO em producao em 2026-08-07.

    Com `voice.status` na lista de adiamento, a tela nao apagou em 200 s de
    observacao: o ciclo de escuta publica esse evento o dia inteiro, com ou
    sem ninguem na sala, e empurrava o prazo para sempre. O descanso de tela
    existia no codigo e nao existia na pratica.
    """
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    controle, _, relogio = _controle(minutos=3.0)
    controle.ligar_no_bus(bus)
    await controle.acordar("display.quadro")

    for estado in ("LISTENING", "TRANSCRIBING", "IDLE") * 40:
        await bus.publish("voice.status", {"estado": estado})
    await bus.aguardar_fila_vazia()

    relogio.avancar(181.0)
    await controle.conferir_prazo()
    assert not controle.acesa, "o ciclo de escuta segurou a tela acesa"

    bus.parar()
    tarefa.cancel()
    try:
        await tarefa
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_quadro_acende_e_voz_nao():
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    controle, _, _ = _controle()
    controle.ligar_no_bus(bus)

    await bus.publish("voice.status", {"estado": "SPEAKING"})
    await bus.aguardar_fila_vazia()
    assert not controle.acesa, "a fala acendeu a tela"

    await bus.publish("display.quadro", {"tipo": "texto", "conteudo": "oi"})
    await bus.aguardar_fila_vazia()
    assert controle.acesa, "o quadro nao acendeu a tela"

    bus.parar()
    tarefa.cancel()
    try:
        await tarefa
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_pedido_de_apagar_apaga_na_hora():
    """'apaga a tela' nao pode esperar os 3 minutos."""
    controle, _, _ = _controle()
    await controle.acordar("teste")
    tratador = controle._fazer_tratador("display.tela", acende=True)
    await tratador(Evento("display.tela", {"ligada": False}))
    assert not controle.acesa


@pytest.mark.asyncio
async def test_sem_xset_o_controle_nao_faz_nada():
    """No Pi (sem X) e na suite, o modulo tem que ser inofensivo."""
    controle = ControleTela(3.0, habilitado=False)
    await controle.acordar("teste")
    await controle.iniciar()
    assert not controle.ativo
    assert not controle.acesa
    await controle.encerrar()


@pytest.mark.asyncio
async def test_xset_que_falha_nao_marca_a_tela_como_acesa():
    """Se o xset falhou, o registro interno nao pode mentir.

    Uma tela marcada como acesa que na verdade esta apagada nunca mais
    receberia um `force on` - o robo ficaria de rosto preto para sempre.
    """
    xset = XsetFalso(funciona=False)
    controle = ControleTela(3.0, executar=xset, habilitado=True)
    controle._habilitado = True
    await controle.acordar("teste")
    assert not controle.acesa
