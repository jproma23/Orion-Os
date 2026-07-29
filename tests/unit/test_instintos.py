"""Testes dos instintos (motion_core/behavior/instintos.py).

O instinto existe para o robo saber dizer "nao da para confiar neste sensor".
Entao o que se testa aqui NAO e so que ele acusa problema - e que ele sabe
ficar calado quando esta tudo bem. Sonda que so sabe dizer sim nao e sonda:
foi exatamente assim que uma varredura de radar com o fio do servo solto
passou por boa em 2026-07-29.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from motion_core.behavior.instintos import InstintoBussola, dispersao_circular_graus
from orion.kernel.event_bus import EventBus, Evento

LIMIAR = 6.0


@pytest_asyncio.fixture
async def bus():
    """Bus COM o laco de despacho rodando.

    Sem isto, `aguardar_fila_vazia()` trava para sempre: `publish` enfileira,
    mas `task_done` so acontece dentro de `iniciar()`. Errei exatamente assim
    na primeira versao deste arquivo, e o sintoma foi a suite pendurar - nao
    um teste vermelho.
    """
    barramento = EventBus()
    tarefa = asyncio.create_task(barramento.iniciar())
    yield barramento
    barramento.parar()
    await tarefa



def _telemetria(**campos) -> Evento:
    return Evento(topico="comm.mensagem.telemetry", dados={"payload": campos})


async def _alimentar(instinto, rumos, em_movimento=False):
    for rumo in rumos:
        await instinto._ao_receber_telemetria(
            _telemetria(rumo_graus=rumo, em_movimento=em_movimento)
        )


# ---------- a matematica circular ----------


def test_dispersao_nao_quebra_no_norte():
    """359 e 1 estao a 2 graus de distancia, nao a 358.

    `max - min` daria 358 e faria um robo olhando para o norte parecer ter a
    pior bussola do sistema - um falso positivo que so aparece numa direcao,
    o tipo de bug que sobrevive meses porque quase nunca reproduz.
    """
    assert dispersao_circular_graus([359.0, 1.0, 0.0, 358.0]) < 3.0


def test_dispersao_de_leituras_estaveis_e_pequena():
    assert dispersao_circular_graus([230.0, 231.0, 229.5, 230.5]) < 1.5


def test_dispersao_de_leituras_espalhadas_e_grande():
    # a faixa real medida sem calibracao em 2026-07-29: 224,4 a 236,7
    assert dispersao_circular_graus([224.4, 236.7, 230.0, 228.0]) > LIMIAR


# ---------- o instinto ----------


@pytest.mark.asyncio
async def test_acusa_rumo_instavel(bus):
    alertas = []
    bus.subscribe("diagnostic.alerta", lambda e: alertas.append(e.dados))
    instinto = InstintoBussola(bus, dispersao_maxima_graus=LIMIAR, amostras=6)

    # dispersao real medida no robo em 2026-07-29, sem calibracao
    await _alimentar(instinto, [224.4, 236.7, 230.1, 228.4, 234.3, 226.1])
    await bus.aguardar_fila_vazia()

    assert alertas, "rumo oscilando 12 graus passou despercebido"
    assert alertas[0]["tipo"] == "rumo_instavel"
    assert alertas[0]["dispersao_graus"] > LIMIAR


@pytest.mark.asyncio
async def test_fica_calado_com_bussola_estavel(bus):
    """A contraprova que importa: sem ela, um instinto que alerta SEMPRE
    passaria no teste acima e seria inutil em campo."""
    alertas = []
    bus.subscribe("diagnostic.alerta", lambda e: alertas.append(e.dados))
    instinto = InstintoBussola(bus, dispersao_maxima_graus=LIMIAR, amostras=6)

    await _alimentar(instinto, [230.0, 230.4, 229.8, 230.2, 230.1, 229.9])
    await bus.aguardar_fila_vazia()

    assert alertas == [], f"alertou com a bussola estavel: {alertas}"


@pytest.mark.asyncio
async def test_nao_mede_com_o_robo_em_movimento(bus):
    """Andando, o rumo muda porque o robo esta girando de verdade - medir ai
    acusaria problema o tempo todo. E os imas permanentes dos motores de passo
    distorcem o campo mesmo desligados."""
    alertas = []
    bus.subscribe("diagnostic.alerta", lambda e: alertas.append(e.dados))
    instinto = InstintoBussola(bus, dispersao_maxima_graus=LIMIAR, amostras=6)

    await _alimentar(instinto, [0.0, 90.0, 180.0, 270.0, 45.0, 135.0], em_movimento=True)
    await bus.aguardar_fila_vazia()

    assert alertas == [], "mediu dispersao com o robo girando"
    assert instinto.dispersao_atual_graus is None


@pytest.mark.asyncio
async def test_nao_alerta_antes_da_janela_encher(bus):
    """Duas leituras nao fazem uma medida de dispersao."""
    alertas = []
    bus.subscribe("diagnostic.alerta", lambda e: alertas.append(e.dados))
    instinto = InstintoBussola(bus, dispersao_maxima_graus=LIMIAR, amostras=6)

    await _alimentar(instinto, [200.0, 260.0])
    await bus.aguardar_fila_vazia()

    assert alertas == []
    assert instinto.dispersao_atual_graus is None


@pytest.mark.asyncio
async def test_alerta_uma_vez_so_enquanto_o_problema_persistir(bus):
    """Um alerta por episodio. Repetir a cada pacote de telemetria afogaria o
    log e a interface, e o problema e o mesmo."""
    alertas = []
    bus.subscribe("diagnostic.alerta", lambda e: alertas.append(e.dados))
    instinto = InstintoBussola(bus, dispersao_maxima_graus=LIMIAR, amostras=4)

    await _alimentar(instinto, [220.0, 240.0, 225.0, 238.0, 221.0, 239.0, 226.0, 237.0])
    await bus.aguardar_fila_vazia()

    assert len(alertas) == 1, f"alertou {len(alertas)} vezes pelo mesmo problema"


@pytest.mark.asyncio
async def test_nao_pede_calibracao_com_a_trava_desligada(bus):
    """Padrao de fabrica: pedir calibracao faz o robo GIRAR 25s sozinho. Com o
    robo preso a cabos (situacao real em 2026-07-29) isso nao pode partir de
    uma decisao automatica."""
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    instinto = InstintoBussola(
        bus, dispersao_maxima_graus=LIMIAR, amostras=4, pedir_calibracao=False
    )

    await _alimentar(instinto, [220.0, 240.0, 225.0, 238.0])
    await bus.aguardar_fila_vazia()

    assert comandos == [], f"mandou o robo girar sozinho: {comandos}"


@pytest.mark.asyncio
async def test_para_de_pedir_calibracao_apos_o_limite(bus):
    """A TRAVA DE LACO. O limiar de 6 graus foi arbitrado sem saber se uma
    bussola calibrada nesta montagem consegue ficar abaixo dele. Se nao
    conseguir, sem esta trava o robo calibraria para sempre: mede 7, calibra,
    mede 7, calibra."""
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    instinto = InstintoBussola(
        bus,
        dispersao_maxima_graus=LIMIAR,
        amostras=4,
        tentativas_max=2,
        intervalo_minimo_s=0.0,  # sem espera, para o teste nao depender do relogio
        pedir_calibracao=True,
    )

    for _ in range(10):
        await _alimentar(instinto, [220.0, 240.0, 225.0, 238.0])
    await bus.aguardar_fila_vazia()

    pedidos = [c for c in comandos if c.get("comando") == "CALIBRATE_COMPASS"]
    assert len(pedidos) == 2, f"pediu calibracao {len(pedidos)} vezes (limite era 2)"
