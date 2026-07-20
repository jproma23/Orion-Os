"""Testes do Motion Core / Navegacao (Cap 12).

Usa ComunicacaoService real + FakeTransporte (mesmo padrao de
test_service.py) com um auto-respondedor de ACK em background, simulando
o firmware sem precisar do Arduino real nem de hardware fisico montado.
"""
import asyncio

import pytest
import pytest_asyncio

from conftest import FakeTransporte
from motion_core.navigation.navigation_core import ErroNavegacao, ModoNavegacao, NavigationCore
from orion.communication.protocol import Mensagem, TipoMensagem
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import Evento, EventBus

CONFIG_MOTION = {
    "max_speed_percent": 60,
    "min_front_distance_cm": 25,
    "mission_timeout_s": 30,
}
CONFIG_NAVIGATION = {
    "patrol_scan_before_segment": True,
    "obstacle_retry_max": 2,
    "follow_lost_timeout_s": 0.3,
    "reduced_speed_near_person_percent": 30,
}


class Cenario:
    """Junta EventBus + ComunicacaoService + NavigationCore + auto-ACK,
    e grava os eventos publicados no bus e os comandos enviados ao
    'hardware_core' para as asserções dos testes."""

    def __init__(self) -> None:
        self.bus = EventBus()
        self.comm = ComunicacaoService("motion_core", self.bus, max_retries=2, ack_timeout_ms=100)
        self.transporte = FakeTransporte()
        self.comm.adicionar_link("hardware_core", self.transporte)
        self.nav = NavigationCore(self.bus, self.comm, CONFIG_MOTION, CONFIG_NAVIGATION)
        self.leituras_scan: list[dict] = []
        self.eventos: list[Evento] = []
        for topico in (
            "navigation.mode_changed",
            "navigation.error",
            "navigation.plan_created",
            "navigation.segment_started",
            "navigation.segment_completed",
            "navigation.obstacle_avoided",
            "navigation.target_lost",
        ):
            self.bus.subscribe(topico, self._gravar_evento)
        self._tarefas: list[asyncio.Task] = []

    async def _gravar_evento(self, evento: Evento) -> None:
        self.eventos.append(evento)

    async def iniciar(self) -> None:
        self._tarefas.append(asyncio.create_task(self.bus.iniciar()))
        self._tarefas.append(asyncio.create_task(self._auto_ack()))

    async def _auto_ack(self) -> None:
        # simula o firmware: ACKa todo COMMAND e, para SCAN_FRONT, publica o
        # motion.scan_complete correspondente (como o Mega real faz, so que
        # instantaneo aqui - sem isso, NavigationCore ficaria travado ate
        # TIMEOUT_SCAN_S esperando um scan_complete que nunca chegaria).
        indice = 0
        while True:
            while indice >= len(self.transporte.enviados):
                await asyncio.sleep(0.005)
            enviado = Mensagem.from_bytes(self.transporte.enviados[indice])
            indice += 1
            if enviado.tipo is TipoMensagem.COMMAND:
                ack = Mensagem.ack(enviado, origem="hardware_core")
                await self.transporte.injetar(ack.to_bytes())
                if enviado.payload.get("comando") == "SCAN_FRONT":
                    await self.bus.publish("motion.scan_complete", {"leituras": self.leituras_scan})

    def definir_leituras_scan(self, leituras: list[dict]) -> None:
        self.leituras_scan = leituras

    def comandos_enviados(self) -> list[str]:
        return [
            Mensagem.from_bytes(bruto).payload.get("comando") for bruto in self.transporte.enviados
        ]

    async def injetar_telemetria(self, distancia_frontal_cm: float) -> None:
        telemetria = Mensagem.nova(
            TipoMensagem.TELEMETRY,
            "hardware_core",
            "motion_core",
            {"distancia_frontal_cm": distancia_frontal_cm, "distancia_frontal_valida": True},
        )
        await self.bus.publish("comm.mensagem.telemetry", telemetria.to_dict())
        await self.bus.aguardar_fila_vazia()

    async def encerrar(self) -> None:
        for tarefa in self._tarefas:
            tarefa.cancel()
        for tarefa in self._tarefas:
            try:
                await tarefa
            except asyncio.CancelledError:
                pass


@pytest_asyncio.fixture
async def cenario():
    c = Cenario()
    await c.iniciar()
    yield c
    await c.encerrar()


@pytest.mark.asyncio
async def test_comeca_em_hold(cenario):
    assert cenario.nav.modo is ModoNavegacao.HOLD


@pytest.mark.asyncio
async def test_manual_move_forward_sem_obstaculo(cenario):
    await cenario.nav.executar_manual("MOVE_FORWARD")
    assert cenario.nav.modo is ModoNavegacao.MANUAL
    assert "MOVE_FORWARD" in cenario.comandos_enviados()


@pytest.mark.asyncio
async def test_manual_espera_hardware_ficar_ocioso_antes_de_mover(cenario):
    # achado real testando com o Mega fisico: mandar um comando de
    # movimento enquanto o anterior ainda esta EXECUTING_MISSION as vezes
    # nao era ACKado - NavigationCore agora espera Estado::IDLE primeiro.
    await cenario.bus.publish("motion.status", {"estado": "EXECUTING_MISSION"})
    await cenario.bus.aguardar_fila_vazia()

    tarefa = asyncio.create_task(cenario.nav.executar_manual("MOVE_FORWARD"))
    await asyncio.sleep(0.2)
    assert "MOVE_FORWARD" not in cenario.comandos_enviados()  # ainda esperando

    await cenario.bus.publish("motion.status", {"estado": "IDLE"})
    await cenario.bus.aguardar_fila_vazia()
    await tarefa
    assert "MOVE_FORWARD" in cenario.comandos_enviados()


@pytest.mark.asyncio
async def test_manual_bloqueia_avanco_com_obstaculo(cenario):
    await cenario.injetar_telemetria(distancia_frontal_cm=10)
    await cenario.nav.executar_manual("MOVE_FORWARD")
    await cenario.bus.aguardar_fila_vazia()
    comandos = cenario.comandos_enviados()
    assert "MOVE_FORWARD" not in comandos
    assert "STOP" in comandos
    assert any(e.topico == "navigation.obstacle_avoided" for e in cenario.eventos)


@pytest.mark.asyncio
async def test_manual_turn_nao_e_bloqueado_por_obstaculo_frontal(cenario):
    await cenario.injetar_telemetria(distancia_frontal_cm=10)
    await cenario.nav.executar_manual("TURN_LEFT")
    assert "TURN_LEFT" in cenario.comandos_enviados()


@pytest.mark.asyncio
async def test_manual_comando_desconhecido_levanta_erro(cenario):
    with pytest.raises(ErroNavegacao):
        await cenario.nav.executar_manual("VOAR")


@pytest.mark.asyncio
async def test_goto_completa_segmento_sem_obstaculo(cenario):
    concluido = await cenario.nav.executar_goto(graus=90, distancia_cm=50)
    await cenario.bus.aguardar_fila_vazia()
    assert concluido is True
    comandos = cenario.comandos_enviados()
    assert comandos == ["TURN_RIGHT", "SCAN_FRONT", "MOVE_DISTANCE"]
    topicos = [e.topico for e in cenario.eventos]
    assert "navigation.plan_created" in topicos
    assert "navigation.segment_started" in topicos
    assert "navigation.segment_completed" in topicos


@pytest.mark.asyncio
async def test_goto_espera_scan_complete_antes_de_mover(cenario):
    # achado real testando com o Mega fisico: SCAN_FRONT demora ~2s pra
    # completar, e mandar MOVE_DISTANCE logo apos o ACK (sem esperar o
    # motion.scan_complete) as vezes colidia com a varredura em andamento.
    cenario.definir_leituras_scan(
        [{"angulo": 90, "distancia_cm": 8, "valida": True}]
    )
    concluido = await cenario.nav.executar_goto(graus=0, distancia_cm=100)
    await cenario.bus.aguardar_fila_vazia()
    assert concluido is False
    assert "MOVE_DISTANCE" not in cenario.comandos_enviados()
    assert any(e.topico == "navigation.obstacle_avoided" for e in cenario.eventos)


@pytest.mark.asyncio
async def test_goto_aborta_com_obstaculo_apos_scan(cenario):
    await cenario.injetar_telemetria(distancia_frontal_cm=5)
    concluido = await cenario.nav.executar_goto(graus=0, distancia_cm=100)
    await cenario.bus.aguardar_fila_vazia()
    assert concluido is False
    assert "MOVE_DISTANCE" not in cenario.comandos_enviados()
    assert any(e.topico == "navigation.obstacle_avoided" for e in cenario.eventos)


@pytest.mark.asyncio
async def test_patrol_percorre_todos_os_segmentos(cenario):
    rota = [{"graus": 0, "distancia_cm": 30}, {"graus": 90, "distancia_cm": 20}]
    await cenario.nav.executar_patrol(rota)
    await cenario.bus.aguardar_fila_vazia()
    concluidos = [e for e in cenario.eventos if e.topico == "navigation.segment_completed"]
    assert len(concluidos) == 2
    assert cenario.nav.modo is ModoNavegacao.HOLD  # volta pro HOLD ao terminar a rota


@pytest.mark.asyncio
async def test_patrol_para_apos_esgotar_tentativas_com_obstaculo(cenario):
    await cenario.injetar_telemetria(distancia_frontal_cm=5)
    rota = [{"graus": 0, "distancia_cm": 30}, {"graus": 90, "distancia_cm": 20}]
    await cenario.nav.executar_patrol(rota)
    await cenario.bus.aguardar_fila_vazia()
    obstaculos = [e for e in cenario.eventos if e.topico == "navigation.obstacle_avoided"]
    assert len(obstaculos) == CONFIG_NAVIGATION["obstacle_retry_max"]
    concluidos = [e for e in cenario.eventos if e.topico == "navigation.segment_completed"]
    assert len(concluidos) == 0  # nunca passou do primeiro segmento


@pytest.mark.asyncio
async def test_follow_gira_em_direcao_a_pessoa_descentralizada(cenario):
    await cenario.nav.iniciar_follow()
    await cenario.bus.publish("vision.person_detected", {"centro_x": 0.9})
    await cenario.bus.aguardar_fila_vazia()
    assert "TURN_RIGHT" in cenario.comandos_enviados()


@pytest.mark.asyncio
async def test_follow_perde_alvo_apos_timeout(cenario):
    await cenario.nav.iniciar_follow()
    await asyncio.sleep(CONFIG_NAVIGATION["follow_lost_timeout_s"] + 0.5)
    await cenario.bus.aguardar_fila_vazia()
    assert cenario.nav.modo is ModoNavegacao.HOLD
    assert any(e.topico == "navigation.target_lost" for e in cenario.eventos)


@pytest.mark.asyncio
async def test_comando_via_event_bus_e_processado(cenario):
    await cenario.bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "STOP"})
    await cenario.bus.aguardar_fila_vazia()
    await asyncio.sleep(0.05)  # STOP e enviado dentro do handler, que roda como task do bus
    assert "STOP" in cenario.comandos_enviados()


@pytest.mark.asyncio
async def test_comando_acao_desconhecida_publica_navigation_error(cenario):
    await cenario.bus.publish("navigation.comando", {"acao": "VOAR"})
    await cenario.bus.aguardar_fila_vazia()
    assert any(e.topico == "navigation.error" for e in cenario.eventos)


@pytest.mark.asyncio
async def test_comando_scan_front_avulso_faz_varredura(cenario):
    # SCAN_FRONT sozinho (Ronda e comando de voz "varredura") deve varrer,
    # nao virar "acao desconhecida" como acontecia antes do conserto.
    await cenario.bus.publish("navigation.comando", {"acao": "SCAN_FRONT"})
    await cenario.bus.aguardar_fila_vazia()
    await asyncio.sleep(0.05)  # o scan roda como task do handler do bus
    assert "SCAN_FRONT" in cenario.comandos_enviados()
    assert not any(e.topico == "navigation.error" for e in cenario.eventos)
