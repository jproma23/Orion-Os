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


def _raios(fundo: float | None = 300.0, **pontos: float) -> list:
    """360 raios a `fundo` cm (uma sala a 3 m), menos os graus dados (g90=30
    -> 30 cm a frente). Fundo medido, e nao "sem eco": desde 01/11 frente cega
    e obstaculo, e uma varredura com um raio so seria frente cega."""
    raios = [fundo] * 360
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
async def test_com_bloquear_false_so_informa_e_nao_bloqueia():
    """O modo "so informa" (01/11 de manha) continua existindo, mas so com
    `bloquear=False` explicito - o padrao passou a bloquear (EDR-0037)."""
    trava = TravaLidar(EventBus(), relogio=Relogio(), bloquear=False)
    await trava._ao_receber_scan(Evento(topico="lidar.scan", dados={"raios_cm": _raios(g90=20.0)}))
    paradas = []

    async def parar():
        paradas.append(True)

    trava.ligar_parada(parar)
    assert trava.motivo_recusa({"comando": "MOVE_FORWARD"}) is None
    await trava._ao_receber_scan(Evento(topico="lidar.scan", dados={"raios_cm": _raios(g90=10.0)}))
    assert paradas == []


# ---------------------------------------------------------------------------
# EDR-0037 (01/11 tarde): freio de avanco continuo. Com a trava em "so avisa"
# o robo bateu na comoda e na parede. Agora ela bloqueia por padrao, para no
# meio do movimento, tem watchdog e piso de 15 cm no manual.
# ---------------------------------------------------------------------------


class _Paradas:
    def __init__(self) -> None:
        self.vezes = 0

    async def __call__(self) -> None:
        self.vezes += 1


async def _scan(trava, raios) -> None:
    await trava._ao_receber_scan(Evento(topico="lidar.scan", dados={"raios_cm": raios}))


def _trava_padrao(relogio):
    trava = TravaLidar(EventBus(), relogio=relogio)
    paradas = _Paradas()
    trava.ligar_parada(paradas)
    return trava, paradas


@pytest.mark.asyncio
async def test_padrao_bloqueia_a_50_cm():
    """yaml sem a chave tem de ficar no lado seguro."""
    relogio = Relogio()
    trava, _ = _trava_padrao(relogio)
    await _scan(trava, _raios(g90=45.0))
    assert "limite 50 cm" in trava.motivo_recusa({"comando": "MOVE_FORWARD"})


@pytest.mark.asyncio
async def test_obstaculo_a_20_cm_durante_avanco_continuo_manda_stop():
    relogio = Relogio()
    trava, paradas = _trava_padrao(relogio)
    await _scan(trava, _raios(g90=200.0))
    assert trava.motivo_recusa({"comando": "MOVE_CONTINUOUS"}) is None
    await _scan(trava, _raios(g90=20.0))
    assert paradas.vezes == 1


@pytest.mark.asyncio
async def test_obstaculo_a_20_cm_durante_giro_nao_para():
    relogio = Relogio()
    trava, paradas = _trava_padrao(relogio)
    await _scan(trava, _raios(g90=200.0))
    assert trava.motivo_recusa({"comando": "TURN_LEFT", "graus": 90}) is None
    await _scan(trava, _raios(g90=20.0))
    assert paradas.vezes == 0


@pytest.mark.asyncio
async def test_giro_depois_de_um_avanco_nao_e_parado():
    """O bug: o avanco em curso nao era apagado pelo giro seguinte."""
    relogio = Relogio()
    trava, paradas = _trava_padrao(relogio)
    await _scan(trava, _raios(g90=200.0))
    trava.motivo_recusa({"comando": "MOVE_DISTANCE", "distancia_cm": 30})
    trava.motivo_recusa({"comando": "TURN_RIGHT", "graus": 60})
    await _scan(trava, _raios(g90=20.0))
    assert paradas.vezes == 0


@pytest.mark.asyncio
async def test_corpo_parado_encerra_o_avanco_depois_de_um_tempo():
    relogio = Relogio()
    trava, paradas = _trava_padrao(relogio)
    await _scan(trava, _raios(g90=200.0))
    trava.motivo_recusa({"comando": "MOVE_DISTANCE", "distancia_cm": 30})
    telemetria = Evento(
        topico="comm.mensagem.telemetry", dados={"payload": {"em_movimento": False}}
    )
    await trava._ao_receber_telemetria(telemetria)  # logo apos o envio: ainda vale
    assert trava._sentido_em_curso == FRENTE
    relogio.agora += 2.0
    await trava._ao_receber_telemetria(telemetria)
    assert trava._sentido_em_curso is None
    await _scan(trava, _raios(g90=20.0))
    assert paradas.vezes == 0


@pytest.mark.asyncio
async def test_watchdog_para_quem_anda_sem_varredura():
    relogio = Relogio()
    trava, paradas = _trava_padrao(relogio)
    await _scan(trava, _raios(g90=200.0))
    trava.motivo_recusa({"comando": "MOVE_CONTINUOUS"})
    relogio.agora += 0.5
    await trava.checar_watchdog()
    assert paradas.vezes == 0
    relogio.agora += 0.2  # 0,7 s sem varredura
    await trava.checar_watchdog()
    assert paradas.vezes == 1


@pytest.mark.asyncio
async def test_watchdog_parado_nao_faz_nada():
    relogio = Relogio()
    trava, paradas = _trava_padrao(relogio)
    await _scan(trava, _raios(g90=200.0))
    relogio.agora += 10.0
    await trava.checar_watchdog()
    assert paradas.vezes == 0


@pytest.mark.asyncio
async def test_manual_obedece_ate_o_piso_de_15_cm_nos_dois_sentidos():
    relogio = Relogio()
    trava, _ = _trava_padrao(relogio)
    await trava._ao_mudar_modo(Evento(topico="behavior.modo_controle", dados={"modo": "manual"}))
    await _scan(trava, _raios(g90=20.0, g270=20.0))
    assert trava.motivo_recusa({"comando": "MOVE_FORWARD"}) is None
    assert trava.motivo_recusa({"comando": "MOVE_DISTANCE", "distancia_cm": -10}) is None
    await _scan(trava, _raios(g90=10.0, g270=10.0))
    assert "limite 15 cm" in trava.motivo_recusa({"comando": "MOVE_FORWARD"})
    assert "limite 15 cm" in trava.motivo_recusa({"comando": "MOVE_DISTANCE", "distancia_cm": -10})


@pytest.mark.asyncio
async def test_re_tambem_freia_a_50_cm_no_autonomo():
    relogio = Relogio()
    trava, _ = _trava_padrao(relogio)
    await _scan(trava, _raios(g270=40.0))
    assert "para tras" in trava.motivo_recusa({"comando": "MOVE_DISTANCE", "distancia_cm": -20})


@pytest.mark.asyncio
async def test_frente_cega_e_obstaculo_para_avancar_mas_nao_para_re_nem_giro():
    """Medido em 01/11: parede a 30 cm de frente some em 3 de 4 varreduras."""
    raios = [200.0] * 360
    for angulo in range(65, 116):
        raios[angulo] = None  # frente cega
    relogio = Relogio()
    trava, paradas = _trava_padrao(relogio)
    await _scan(trava, raios)
    assert "frente cega" in trava.motivo_recusa({"comando": "MOVE_FORWARD"})
    assert trava.motivo_recusa({"comando": "MOVE_DISTANCE", "distancia_cm": -10}) is None
    assert trava.motivo_recusa({"comando": "TURN_LEFT", "graus": 30}) is None


@pytest.mark.asyncio
async def test_frente_fica_cega_no_meio_do_avanco_para():
    relogio = Relogio()
    trava, paradas = _trava_padrao(relogio)
    await _scan(trava, [200.0] * 360)
    assert trava.motivo_recusa({"comando": "MOVE_CONTINUOUS"}) is None
    cega = [200.0] * 360
    for angulo in range(65, 116):
        cega[angulo] = None
    await _scan(trava, cega)
    assert paradas.vezes == 1


# --- 08/11: o proprio corpo nao e obstaculo; frente cega solta nao para ---


@pytest.mark.asyncio
async def test_ponto_dentro_da_carcaca_nao_trava():
    """Medido em 08/11: 75 paradas por 'obstaculo a 1 cm para frente' - um
    ponto a 20 cm quase de lado (88 graus do eixo) tem 0,7 cm de avanco. Com o
    LiDAR 8 cm atras da ponta, ele esta dentro/ao lado da carcaca."""
    raios = _raios(g2=20.0)  # 88 graus do eixo da frente (90)
    trava = TravaLidar(EventBus(), relogio=Relogio(), corpo_frente_cm=8.0)
    await _scan(trava, raios)
    assert trava.motivo_recusa({"comando": "MOVE_FORWARD"}) is None


@pytest.mark.asyncio
async def test_obstaculo_logo_alem_da_ponta_ainda_trava():
    trava = TravaLidar(EventBus(), relogio=Relogio(), corpo_frente_cm=8.0)
    await _scan(trava, _raios(g90=10.0))
    assert "obstaculo a 10 cm" in trava.motivo_recusa({"comando": "MOVE_FORWARD"})


@pytest.mark.asyncio
async def test_sem_corpo_configurado_mantem_o_comportamento_antigo():
    trava = TravaLidar(EventBus(), relogio=Relogio())
    await _scan(trava, _raios(g2=20.0))
    assert trava.motivo_recusa({"comando": "MOVE_FORWARD"}) is not None


def _cega():
    cega = [200.0] * 360
    for angulo in range(65, 116):
        cega[angulo] = None
    return cega


@pytest.mark.asyncio
async def test_uma_varredura_cega_solta_no_avanco_nao_para():
    relogio = Relogio()
    trava = TravaLidar(EventBus(), relogio=relogio, cegas_para_parar=2)
    paradas = _Paradas()
    trava.ligar_parada(paradas)
    await _scan(trava, [200.0] * 360)
    assert trava.motivo_recusa({"comando": "MOVE_CONTINUOUS"}) is None
    await _scan(trava, _cega())
    await _scan(trava, [200.0] * 360)
    await _scan(trava, _cega())
    assert paradas.vezes == 0


@pytest.mark.asyncio
async def test_duas_varreduras_cegas_seguidas_param():
    relogio = Relogio()
    trava = TravaLidar(EventBus(), relogio=relogio, cegas_para_parar=2)
    paradas = _Paradas()
    trava.ligar_parada(paradas)
    await _scan(trava, [200.0] * 360)
    assert trava.motivo_recusa({"comando": "MOVE_CONTINUOUS"}) is None
    await _scan(trava, _cega())
    await _scan(trava, _cega())
    assert paradas.vezes == 1


@pytest.mark.asyncio
async def test_obstaculo_perto_para_na_hora_mesmo_contando_cegas():
    relogio = Relogio()
    trava = TravaLidar(EventBus(), relogio=relogio, cegas_para_parar=2)
    paradas = _Paradas()
    trava.ligar_parada(paradas)
    await _scan(trava, [200.0] * 360)
    assert trava.motivo_recusa({"comando": "MOVE_CONTINUOUS"}) is None
    await _scan(trava, _raios(g90=20.0))
    assert paradas.vezes == 1


@pytest.mark.asyncio
async def test_sair_andando_com_frente_cega_continua_recusado():
    trava = TravaLidar(EventBus(), relogio=Relogio(), cegas_para_parar=2)
    await _scan(trava, _cega())
    assert "frente cega" in trava.motivo_recusa({"comando": "MOVE_FORWARD"})
