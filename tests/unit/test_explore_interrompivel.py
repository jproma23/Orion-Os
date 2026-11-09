"""O trecho do explore e interrompivel (revisao de 2026-07-30) e desvia
sozinho pelo LiDAR (2026-11-01, EDR-0037)."""

import math
import time

import pytest

from motion_core.navigation import navigation_core as nav


def _raios(frente_cm: float, resto_cm: float = 300.0) -> list:
    """Parede reta a `frente_cm` a frente (raio = d / cos), resto a `resto_cm`."""
    raios = [resto_cm] * 360
    for delta in range(-60, 61):
        raios[90 + delta] = min(resto_cm, frente_cm / math.cos(math.radians(delta)))
    return raios


class NavFalsa:
    """So o que o `executar_explore` usa - sem bus, sem hardware."""

    def __init__(self, raios) -> None:
        self._comando_pendente = None
        self._modo = None
        self._raios_lidar = raios
        self._raios_recebido_em = time.monotonic()
        self._limite_desvio_cm = 50.0
        self._meia_largura_desvio_cm = 25.0
        self.avancos: list[float] = []
        self.giros: list[tuple[float, str]] = []
        self.obstaculos: list[str] = []
        self.parar_no_avanco = None
        #: (o que aconteceu, valor) na ordem: "olhar" pan, "girar" graus.
        self.ordem: list[tuple[str, float]] = []
        self._event_bus = _BusFalso()

    async def _olhar_para(self, graus):
        self.ordem.append(("olhar", graus))

    async def _mudar_modo(self, modo):
        self._modo = modo

    async def _publicar_obstaculo(self, contexto, motivo="obstaculo_frontal", distancia_cm=None):
        self.obstaculos.append(motivo)

    async def _avancar_explore(self, distancia_cm):
        self.avancos.append(distancia_cm)
        if self.parar_no_avanco == len(self.avancos):
            self._comando_pendente = {"acao": "MANUAL", "comando": "STOP"}
        return True

    async def girar(self, graus, sentido):
        self.giros.append((graus, sentido))
        self.ordem.append(("girar", graus if sentido == "DIREITA" else -graus))
        # depois do giro, a frente fica aberta
        self._raios_lidar = _raios(300.0)
        self._raios_recebido_em = time.monotonic()

    async def _aguardar_ocioso(self):
        pass

    async def _aguardar_movimento_terminar(self):
        pass

    executar_explore = nav.NavigationCore.executar_explore
    _girar_explore = nav.NavigationCore._girar_explore


class _BusFalso:
    def __init__(self) -> None:
        self.publicados: list[tuple[str, dict]] = []

    async def publish(self, topico, dados, **kwargs):
        self.publicados.append((topico, dados))


@pytest.fixture(autouse=True)
def _sem_espera(monkeypatch):
    monkeypatch.setattr(nav, "ESPERA_VARREDURA_APOS_GIRO_S", 0.0)


@pytest.mark.asyncio
async def test_explore_encerra_o_trecho_quando_chega_comando():
    """O PARAR nao pode esperar o trecho inteiro: anda no maximo UM passo a mais."""
    robo = NavFalsa(_raios(300.0))
    robo.parar_no_avanco = 1
    await robo.executar_explore()
    assert len(robo.avancos) == 1


@pytest.mark.asyncio
async def test_explore_com_a_comoda_a_25_cm_gira_e_nao_avanca_para_ela():
    """O caso de 01/11 16:20: com 25,7 cm livres ele mandou 30 cm e bateu."""
    robo = NavFalsa(_raios(25.7))
    robo.parar_no_avanco = 1
    await robo.executar_explore()
    assert robo.giros, "frente curta tem de virar giro"
    assert robo.avancos == [30.0], "so avanca depois do giro, com a frente aberta"


@pytest.mark.asyncio
async def test_explore_nunca_manda_mais_do_que_cabe():
    robo = NavFalsa(_raios(70.0))
    robo.parar_no_avanco = 1
    await robo.executar_explore()
    assert robo.avancos == [20.0]


@pytest.mark.asyncio
async def test_explore_cercado_avisa_a_ia_e_nao_mexe():
    robo = NavFalsa(_raios(30.0, resto_cm=30.0))
    await robo.executar_explore()
    assert robo.avancos == [] and robo.giros == []
    assert robo.obstaculos == ["obstaculo_frontal"]


@pytest.mark.asyncio
async def test_explore_sem_varredura_recente_nao_anda_as_cegas():
    robo = NavFalsa(_raios(300.0))
    robo._raios_recebido_em = time.monotonic() - 10.0
    await robo.executar_explore()
    assert robo.avancos == []
    assert robo.obstaculos == ["frente_desconhecida"]


@pytest.mark.asyncio
async def test_espera_o_movimento_comecar_antes_de_esperar_ocioso(monkeypatch):
    """O caso de 01/11 17:31: logo apos o envio o corpo ainda esta IDLE, e a
    espera antiga voltava na hora - dois giros sairam no mesmo segundo."""
    import asyncio

    monkeypatch.setattr(nav, "INTERVALO_CHECAGEM_OCIOSO_S", 0.01)
    estados = iter(["IDLE", "IDLE", "EXECUTING_MISSION", "EXECUTING_MISSION", "IDLE"])

    class Corpo:
        _estado_hardware = "IDLE"
        esperou_ocioso_com = None

        async def _aguardar_ocioso(self):
            self.esperou_ocioso_com = self._estado_hardware

        _aguardar_movimento_terminar = nav.NavigationCore._aguardar_movimento_terminar

    corpo = Corpo()

    async def mudar():
        for estado in estados:
            corpo._estado_hardware = estado
            await asyncio.sleep(0.02)

    tarefa = asyncio.create_task(mudar())
    await corpo._aguardar_movimento_terminar()
    tarefa.cancel()
    assert corpo.esperou_ocioso_com == "EXECUTING_MISSION", (
        "so pode esperar o fim depois de ver o movimento comecar"
    )


@pytest.mark.asyncio
async def test_explore_segue_andando_e_desviando_alem_de_6_passos():
    robo = NavFalsa(_raios(300.0))
    robo.parar_no_avanco = 10
    await robo.executar_explore()
    assert len(robo.avancos) == 10, "o trecho antigo parava no 6o passo"


@pytest.mark.asyncio
async def test_a_cabeca_olha_para_o_lado_do_giro_antes_de_girar_e_volta():
    """08/11 (dono: auto alinhamento): a camera olha para onde ele vai."""
    nav_falsa = NavFalsa(_raios(25.0))  # parede a 25 cm: tem que girar
    nav_falsa._comando_pendente = None
    await nav_falsa.executar_explore()
    giro = next(i for i, (o, _) in enumerate(nav_falsa.ordem) if o == "girar")
    lado = nav_falsa.ordem[giro][1]
    assert nav_falsa.ordem[giro - 1] == ("olhar", lado)
    assert nav_falsa.ordem[giro + 1] == ("olhar", 0.0)


@pytest.mark.asyncio
async def test_fim_do_trecho_pede_a_ia_na_hora():
    nav_falsa = NavFalsa(_raios(300.0))
    await nav_falsa.executar_explore()
    assert ("behavior.reconsiderar", {"origem": "explore", "motivo": "trecho concluido"}) in nav_falsa._event_bus.publicados
