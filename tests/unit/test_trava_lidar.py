"""Trava do LiDAR (2026-11-01): nenhum comando de roda anda para cima de
obstaculo, venha de quem vier. Caso real que a originou: a acao falada do
Notebook mandou MOVE_FORWARD pelo roteamento transparente do Pi, sem olhar o
LiDAR, e o robo bateu.
"""
import asyncio

import pytest

from motion_core.navigation.trava_lidar import FRENTE, TRAS, TravaLidar, sentido_do_comando
from orion.communication.protocol import Mensagem, TipoMensagem
from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.kernel.event_bus import EventBus, Evento

from conftest import FakeTransporte


class Relogio:
    def __init__(self) -> None:
        self.agora = 100.0

    def __call__(self) -> float:
        return self.agora


def _raios(**pontos: float) -> list:
    """360 raios sem eco, menos os graus dados (g90=30 -> 30 cm a frente)."""
    raios = [None] * 360
    for nome, distancia in pontos.items():
        raios[int(nome[1:])] = distancia
    return raios


async def _trava_com_scan(raios, relogio=None) -> TravaLidar:
    trava = TravaLidar(EventBus(), relogio=relogio or Relogio(), bloquear=True)
    await trava._ao_receber_scan(Evento(topico="lidar.scan", dados={"raios_cm": raios}))
    return trava


def test_sentido_de_cada_comando():
    assert sentido_do_comando({"comando": "MOVE_FORWARD"}) == FRENTE
    assert sentido_do_comando({"comando": "MOVE_CONTINUOUS"}) == FRENTE
    assert sentido_do_comando({"comando": "MOVE_DISTANCE", "distancia_cm": -20}) == TRAS
    assert sentido_do_comando({"comando": "CMD_VEL", "v_m_s": 0.1, "w_rad_s": 0}) == FRENTE
    assert sentido_do_comando({"comando": "CMD_VEL", "v_m_s": -0.1, "w_rad_s": 0}) == TRAS
    assert sentido_do_comando({"comando": "CMD_VEL", "v_m_s": 0.0, "w_rad_s": 0.5}) is None
    assert sentido_do_comando({"comando": "TURN_LEFT"}) is None
    assert sentido_do_comando({"comando": "STOP"}) is None


@pytest.mark.asyncio
async def test_obstaculo_a_frente_recusa_andar_para_frente():
    trava = await _trava_com_scan(_raios(g90=30.0))
    assert "obstaculo a 30 cm" in trava.motivo_recusa({"comando": "MOVE_FORWARD"})


@pytest.mark.asyncio
async def test_obstaculo_a_frente_nao_impede_re_nem_giro():
    trava = await _trava_com_scan(_raios(g90=30.0))
    assert trava.motivo_recusa({"comando": "MOVE_DISTANCE", "distancia_cm": -20}) is None
    assert trava.motivo_recusa({"comando": "TURN_LEFT"}) is None
    assert trava.motivo_recusa({"comando": "STOP"}) is None


@pytest.mark.asyncio
async def test_obstaculo_atras_recusa_a_re():
    trava = await _trava_com_scan(_raios(g270=20.0))
    assert trava.motivo_recusa({"comando": "MOVE_DISTANCE", "distancia_cm": -20}) is not None
    assert trava.motivo_recusa({"comando": "MOVE_FORWARD"}) is None


@pytest.mark.asyncio
async def test_parede_ao_lado_fora_do_corredor_nao_trava():
    # 30 cm na lateral pura (0 = esquerda): fora da meia largura de 25 cm a
    # frente - corredor estreito nao pode imobilizar o robo.
    trava = await _trava_com_scan(_raios(g0=30.0, g180=30.0))
    assert trava.motivo_recusa({"comando": "MOVE_FORWARD"}) is None


@pytest.mark.asyncio
async def test_obstaculo_longe_deixa_andar():
    trava = await _trava_com_scan(_raios(g90=120.0))
    assert trava.motivo_recusa({"comando": "MOVE_FORWARD"}) is None


def test_sem_lidar_nao_anda_as_cegas():
    trava = TravaLidar(EventBus(), bloquear=True)
    assert "sem leitura" in trava.motivo_recusa({"comando": "MOVE_FORWARD"})
    assert trava.motivo_recusa({"comando": "STOP"}) is None


@pytest.mark.asyncio
async def test_leitura_velha_nao_anda_as_cegas():
    relogio = Relogio()
    trava = await _trava_com_scan(_raios(g90=120.0), relogio)
    relogio.agora += 5.0
    assert "velha" in trava.motivo_recusa({"comando": "MOVE_FORWARD"})


@pytest.mark.asyncio
async def test_obstaculo_que_aparece_no_meio_do_movimento_manda_stop():
    trava = await _trava_com_scan(_raios(g90=150.0))
    paradas = []

    async def parar():
        paradas.append(True)

    trava.ligar_parada(parar)
    assert trava.motivo_recusa({"comando": "CMD_VEL", "v_m_s": 0.1, "w_rad_s": 0}) is None
    await trava._ao_receber_scan(Evento(topico="lidar.scan", dados={"raios_cm": _raios(g90=25.0)}))
    assert paradas == [True]


@pytest.mark.asyncio
async def test_porteiro_recusa_no_send_local():
    bus = EventBus()
    servico = ComunicacaoService("motion_core", bus)
    servico.adicionar_link("hardware_core", FakeTransporte())
    servico.definir_porteiro("hardware_core", lambda payload: "obstaculo")
    with pytest.raises(ErroComunicacao, match="recusado"):
        await servico.send("hardware_core", {"comando": "MOVE_FORWARD"})
    await servico.encerrar()


@pytest.mark.asyncio
async def test_porteiro_barra_o_comando_repassado_do_notebook_e_devolve_nack():
    """O caso de 01/11: MOVE_FORWARD do Notebook atravessava o Pi sem olhar."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    servico = ComunicacaoService("motion_core", bus)
    link_notebook = FakeTransporte()
    link_esp32 = FakeTransporte()
    servico.adicionar_link("mission_core", link_notebook)
    servico.adicionar_link("hardware_core", link_esp32)
    servico.definir_porteiro("hardware_core", lambda payload: "obstaculo a 30 cm")

    pedido = Mensagem.nova(
        TipoMensagem.COMMAND, "mission_core", "hardware_core", {"comando": "MOVE_FORWARD"}
    )
    await link_notebook.injetar(pedido.to_bytes())
    await asyncio.sleep(0.05)

    assert link_esp32.enviados == []  # nao chegou nas rodas
    nack = Mensagem.from_bytes(link_notebook.enviados[0])
    assert nack.tipo is TipoMensagem.NACK
    assert nack.payload["motivo"] == "obstaculo a 30 cm"

    await servico.encerrar()
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_por_padrao_so_informa_e_nao_bloqueia():
    """Decisao do dono (2026-11-01): nada bloqueia; a IA ve e desvia."""
    trava = TravaLidar(EventBus(), relogio=Relogio())
    await trava._ao_receber_scan(Evento(topico="lidar.scan", dados={"raios_cm": _raios(g90=20.0)}))
    paradas = []

    async def parar():
        paradas.append(True)

    trava.ligar_parada(parar)
    assert trava.motivo_recusa({"comando": "MOVE_FORWARD"}) is None
    await trava._ao_receber_scan(Evento(topico="lidar.scan", dados={"raios_cm": _raios(g90=10.0)}))
    assert paradas == []
