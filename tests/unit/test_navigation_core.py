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

    def __init__(self, consulta=None) -> None:
        self.bus = EventBus()
        self.comm = ComunicacaoService("motion_core", self.bus, max_retries=2, ack_timeout_ms=100)
        self.transporte = FakeTransporte()
        self.comm.adicionar_link("hardware_core", self.transporte)
        self.nav = NavigationCore(
            self.bus, self.comm, CONFIG_MOTION, CONFIG_NAVIGATION, consultar_repeticoes=consulta
        )
        self.leituras_scan: list[dict] = []
        self.eventos: list[Evento] = []
        for topico in (
            "navigation.mode_changed",
            "navigation.error",
            "navigation.plan_created",
            "navigation.segment_started",
            "navigation.segment_completed",
            "navigation.obstacle_avoided",
            "navigation.obstacle_recorrente",
            "navigation.target_lost",
            "navigation.risco_assumido",
        ):
            self.bus.subscribe(topico, self._gravar_evento)
        self._tarefas: list[asyncio.Task] = []

    async def _gravar_evento(self, evento: Evento) -> None:
        self.eventos.append(evento)

    async def iniciar(self) -> None:
        self._tarefas.append(asyncio.create_task(self.bus.iniciar()))
        self._tarefa_auto_ack = asyncio.create_task(self._auto_ack())
        self._tarefas.append(self._tarefa_auto_ack)

    async def parar_auto_ack(self) -> None:
        """Para de ACKar comandos - usado pra simular o link com o Arduino
        falhando de verdade (comm.send esgota as retransmissoes e levanta
        ErroComunicacao), sem precisar mexer no FakeTransporte
        compartilhado com outros arquivos de teste."""
        self._tarefa_auto_ack.cancel()
        try:
            await self._tarefa_auto_ack
        except asyncio.CancelledError:
            pass

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
        """Faz o robo MEDIR `distancia_frontal_cm` a frente.

        Passou a injetar uma VARREDURA DE LIDAR em 2026-09-19, quando os
        ultrassons sairam do robo. O nome e a assinatura ficaram: o que estes
        testes cobram e o comportamento diante de uma distancia medida, e isso
        nao mudou de sensor - so mudou quem mede.
        """
        await self.injetar_lidar(distancia_frontal_cm)

    async def injetar_lidar(self, distancia_frontal_cm: float | None) -> None:
        """Publica um `lidar.scan` com a frente medindo o que se pediu.

        `None` = setores frontais SEM ECO, que e como o LiDAR relata tanto
        "nada refletiu" quanto "perto demais" (abaixo de `min_distancia_mm`).
        E o equivalente exato do `frontal_sem_eco` do ultrassom, e serve as
        mesmas regressoes: "nao medi" nunca pode virar "esta livre".

        Convencao de angulo do projeto: 0° = esquerda, 90° = FRENTE,
        180° = direita. A janela frontal e 90° +- 60°, ou seja 30°..150°.
        """
        setores = []
        for i in range(12):
            inicio = i * 30.0
            centro = inicio + 15.0
            na_frente = abs((centro - 90.0 + 180.0) % 360.0 - 180.0) <= 60.0
            if na_frente:
                distancia = distancia_frontal_cm
            else:
                # Atras/laterais longe de proposito: assim o minimo frontal e
                # exatamente o valor pedido, e um erro na janela de angulo
                # aparece como teste vermelho em vez de passar por acaso.
                distancia = 999.0
            setores.append(
                {"inicio_graus": inicio, "fim_graus": inicio + 30.0, "distancia_cm": distancia}
            )
        await self.bus.publish("lidar.scan", {"valido": True, "setores": setores})
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
async def test_velocidade_do_nav2_nao_espera_ocioso(cenario):
    """CMD_VEL (2026-10-31): o Nav2 manda a 10 Hz, e cada mensagem e a
    continuacao da anterior. Esperar o hardware ficar IDLE - como o MANUAL
    faz - pararia o robo entre duas mensagens."""
    await cenario.bus.publish("motion.status", {"estado": "EXECUTING_MISSION"})
    await cenario.bus.aguardar_fila_vazia()

    await asyncio.wait_for(cenario.nav.definir_velocidade(0.05, 0.2), timeout=1.0)

    assert cenario.nav.modo is ModoNavegacao.MANUAL
    enviado = Mensagem.from_bytes(cenario.transporte.enviados[-1]).payload
    assert enviado == {"comando": "CMD_VEL", "v_m_s": 0.05, "w_rad_s": 0.2}


@pytest.mark.asyncio
async def test_manual_obedece_mesmo_com_obstaculo_frontal(cenario):
    """MANUAL OBEDECE 100% (dono, 2026-08-18): comando do operador passa
    mesmo com obstaculo medido - o obstaculo vira evento de informacao e
    aviso, nunca um STOP que engole o comando."""
    await cenario.injetar_telemetria(distancia_frontal_cm=10)
    await cenario.nav.executar_manual("MOVE_FORWARD")
    await cenario.bus.aguardar_fila_vazia()
    comandos = cenario.comandos_enviados()
    assert "MOVE_FORWARD" in comandos
    assert "STOP" not in comandos
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


# ---------- vocabulario manual ampliado (2026-07-27) ----------
#
# MOVE_CONTINUOUS, MOVE_DISTANCE, FAN_ON e FAN_OFF entraram em COMANDOS_MANUAIS
# para bater com o que a webui ja aceitava (COMANDOS_PERMITIDOS) - ate aqui o
# /comando falava com o Hardware Core direto, sem passar por executar_manual,
# entao esse descompasso nunca tinha sido pego por teste nenhum.


@pytest.mark.asyncio
async def test_manual_move_continuous_obedece_mesmo_com_obstaculo(cenario):
    # o joystick usa MOVE_CONTINUOUS (nao MOVE_FORWARD) para andar para
    # frente - precisa da MESMA regra: operador manda, o robo obedece.
    await cenario.injetar_telemetria(distancia_frontal_cm=10)
    await cenario.nav.executar_manual("MOVE_CONTINUOUS")
    await cenario.bus.aguardar_fila_vazia()
    comandos = cenario.comandos_enviados()
    assert "MOVE_CONTINUOUS" in comandos
    assert "STOP" not in comandos
    assert any(e.topico == "navigation.obstacle_avoided" for e in cenario.eventos)


@pytest.mark.asyncio
async def test_manual_move_distance_nao_e_bloqueado_por_obstaculo_frontal(cenario):
    # a re (MOVE_DISTANCE negativo) nao tem sensor traseiro na seguranca
    # reativa - checar obstaculo A FRENTE antes de andar PARA TRAS nao faz
    # sentido, e bloquear aqui deixaria o joystick sem re nenhuma quando
    # o robo estivesse parado perto de algo na frente.
    await cenario.injetar_telemetria(distancia_frontal_cm=10)
    await cenario.nav.executar_manual("MOVE_DISTANCE", distancia_cm=-8.0)
    assert "MOVE_DISTANCE" in cenario.comandos_enviados()


@pytest.mark.asyncio
async def test_manual_move_distance_encaminha_distancia_cm(cenario):
    await cenario.nav.executar_manual("MOVE_DISTANCE", velocidade_percent=40, distancia_cm=-8.0)
    ultimo = Mensagem.from_bytes(cenario.transporte.enviados[-1])
    assert ultimo.payload.get("distancia_cm") == -8.0
    assert ultimo.payload.get("velocidade_percent") == 40


@pytest.mark.asyncio
async def test_manual_fan_on_e_aceito(cenario):
    await cenario.nav.executar_manual("FAN_ON")
    assert "FAN_ON" in cenario.comandos_enviados()


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


async def _iniciar_cenario_com_consulta(consulta):
    c = Cenario(consulta=consulta)
    await c.iniciar()
    return c


@pytest.mark.asyncio
async def test_patrol_aborta_sem_repetir_quando_mesma_situacao_ja_falhou():
    """Ciclo fechado: depois de UMA falha de avanco, a consulta diz que a
    mesma situacao ja falhou (>= limite na janela). O robo NAO repete o erro:
    aborta a rota na primeira tentativa e publica o relato para o dono."""
    chamadas: list[tuple[str, float]] = []

    async def consulta(situacao, janela_s):
        chamadas.append((situacao, janela_s))
        return 3  # >= limite

    c = await _iniciar_cenario_com_consulta(consulta)
    try:
        await c.injetar_telemetria(distancia_frontal_cm=5)
        rota = [{"graus": 0, "distancia_cm": 30}, {"graus": 90, "distancia_cm": 20}]
        await c.nav.executar_patrol(rota)
        await c.bus.aguardar_fila_vazia()
        # Uma unica tentativa: nao houve retry cego.
        obstaculos = [e for e in c.eventos if e.topico == "navigation.obstacle_avoided"]
        assert len(obstaculos) == 1
        assert len([e for e in c.eventos if e.topico == "navigation.segment_completed"]) == 0
        # O relato para o dono saiu com os dados do degrau.
        relatos = [e for e in c.eventos if e.topico == "navigation.obstacle_recorrente"]
        assert len(relatos) == 1
        assert relatos[0].dados["situacao"] == "avancar"
        assert relatos[0].dados["repeticoes"] == 3
        assert chamadas == [("avancar", 300.0)]
    finally:
        await c.encerrar()


@pytest.mark.asyncio
async def test_patrol_repete_como_antes_quando_consulta_abaixo_do_limite():
    """Repeticao abaixo do limite nao muda nada: a rajada de tentativas segue
    o limite por segmento de sempre."""
    async def consulta(situacao, janela_s):
        return 1  # < limite

    c = await _iniciar_cenario_com_consulta(consulta)
    try:
        await c.injetar_telemetria(distancia_frontal_cm=5)
        rota = [{"graus": 0, "distancia_cm": 30}, {"graus": 90, "distancia_cm": 20}]
        await c.nav.executar_patrol(rota)
        await c.bus.aguardar_fila_vazia()
        obstaculos = [e for e in c.eventos if e.topico == "navigation.obstacle_avoided"]
        assert len(obstaculos) == CONFIG_NAVIGATION["obstacle_retry_max"]
        assert len([e for e in c.eventos if e.topico == "navigation.obstacle_recorrente"]) == 0
    finally:
        await c.encerrar()


@pytest.mark.asyncio
async def test_patrol_consulta_falhando_nao_muda_comportamento():
    """Memoria fora nao pode travar o robo: consulta que levanta excecao vira
    'siga como antes' - a mesma regra de todos os degraus locais do projeto."""
    async def consulta(situacao, janela_s):
        raise RuntimeError("banco fora")

    c = await _iniciar_cenario_com_consulta(consulta)
    try:
        await c.injetar_telemetria(distancia_frontal_cm=5)
        rota = [{"graus": 0, "distancia_cm": 30}]
        await c.nav.executar_patrol(rota)
        await c.bus.aguardar_fila_vazia()
        obstaculos = [e for e in c.eventos if e.topico == "navigation.obstacle_avoided"]
        assert len(obstaculos) == CONFIG_NAVIGATION["obstacle_retry_max"]
        assert len([e for e in c.eventos if e.topico == "navigation.obstacle_recorrente"]) == 0
    finally:
        await c.encerrar()


@pytest.mark.asyncio
async def test_follow_gira_em_direcao_a_pessoa_descentralizada(cenario):
    await cenario.nav.iniciar_follow()
    await cenario.bus.publish("vision.person_recognized", {"centro_x": 0.9})
    await cenario.bus.aguardar_fila_vazia()
    assert "TURN_RIGHT" in cenario.comandos_enviados()


@pytest.mark.asyncio
async def test_follow_ignora_pessoa_nao_reconhecida(cenario):
    """FOLLOW so anda atras de quem o Vision Core reconhece (2026-07-28) -
    vision.person_detected (qualquer pessoa, reconhecida ou nao) nao deve
    mover o robo; so vision.person_recognized move."""
    await cenario.nav.iniciar_follow()
    await cenario.bus.publish("vision.person_detected", {"caixa": (500, 100, 600, 300)})
    await cenario.bus.aguardar_fila_vazia()
    assert cenario.comandos_enviados() == []


@pytest.mark.asyncio
async def test_follow_perde_alvo_apos_timeout(cenario):
    await cenario.nav.iniciar_follow()
    await asyncio.sleep(CONFIG_NAVIGATION["follow_lost_timeout_s"] + 0.5)
    await cenario.bus.aguardar_fila_vazia()
    assert cenario.nav.modo is ModoNavegacao.HOLD
    assert any(e.topico == "navigation.target_lost" for e in cenario.eventos)


@pytest.mark.asyncio
async def test_follow_sai_do_modo_mesmo_se_comm_falhar_ao_avisar(cenario):
    """Achado real da vistoria de 2026-07-24: _monitorar_perda_de_alvo
    roda solta (asyncio.create_task), fora do unico ponto que tratava
    ErroComunicacao. Se o comm.send do STOP/SCAN_FRONT falhasse (ex.:
    serial com o Arduino caida bem na hora), a excecao subia sem
    tratamento e matava a task silenciosamente - o modo ficava travado em
    FOLLOW pra sempre e navigation.target_lost nunca era publicado."""
    await cenario.nav.iniciar_follow()
    await cenario.parar_auto_ack()  # simula o link com o Arduino caido

    await asyncio.sleep(CONFIG_NAVIGATION["follow_lost_timeout_s"] + 0.6)
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
    # Desde 2026-07-28 a execucao roda FORA do bus (ver _ao_receber_comando):
    # esvaziar a fila do bus nao basta mais para saber que o comando foi
    # tratado, e o navigation.error e publicado depois, pela task.
    await asyncio.sleep(0.05)
    await cenario.bus.aguardar_fila_vazia()
    assert any(e.topico == "navigation.error" for e in cenario.eventos)


@pytest.mark.asyncio
async def test_comando_manual_mais_recente_descarta_o_anterior(cenario):
    """Joystick: comando velho e DESCARTADO, nunca executado atrasado.

    Nao ha fila de comandos manuais de proposito. Uma fila faria o robo
    continuar andando depois de o operador soltar o joystick - inaceitavel na
    re, que nao tem seguranca reativa (o SafetyManager so olha o sensor
    frontal). Soltar manda STOP, e o STOP ganha por ser o mais recente.

    Chama o handler direto: nenhum dos dois `await` suspende, entao a task de
    execucao so comeca depois dos dois - o segundo comando substitui o
    primeiro antes de qualquer envio. Deterministico, sem depender de tempo.
    """
    await cenario.nav._ao_receber_comando(
        Evento(topico="navigation.comando", dados={"acao": "MANUAL", "comando": "MOVE_FORWARD"})
    )
    await cenario.nav._ao_receber_comando(
        Evento(topico="navigation.comando", dados={"acao": "MANUAL", "comando": "STOP"})
    )
    await asyncio.sleep(0.05)

    enviados = cenario.comandos_enviados()
    assert "STOP" in enviados
    assert "MOVE_FORWARD" not in enviados


@pytest.mark.asyncio
async def test_comando_scan_front_avulso_faz_varredura(cenario):
    # SCAN_FRONT sozinho (Ronda e comando de voz "varredura") deve varrer,
    # nao virar "acao desconhecida" como acontecia antes do conserto.
    await cenario.bus.publish("navigation.comando", {"acao": "SCAN_FRONT"})
    await cenario.bus.aguardar_fila_vazia()
    await asyncio.sleep(0.05)  # o scan roda como task do handler do bus
    assert "SCAN_FRONT" in cenario.comandos_enviados()
    assert not any(e.topico == "navigation.error" for e in cenario.eventos)


@pytest.mark.asyncio
async def test_sair_de_follow_pelo_joystick_mata_o_vigia_de_perda_de_alvo(cenario):
    """Ate 2026-07-28 so o definir_modo_hold cancelava _tarefa_follow, e isso
    nao doia porque nenhum modo alem do MANUAL era alcancavel pela interface.
    Com o seletor de modo o vazamento fica a um toque.

    Cenario: operador em FOLLOW pega o joystick. O modo vira MANUAL, mas o
    vigia continuava vivo e, passado o follow_lost_timeout_s sem deteccao,
    mandava STOP e forcava HOLD - o robo morria na mao do operador, sem nada
    na tela explicando por que.
    """
    await cenario.nav.iniciar_follow()
    vigia = cenario.nav._tarefa_follow
    assert vigia is not None

    await cenario.nav.executar_manual("TURN_LEFT")
    assert cenario.nav.modo is ModoNavegacao.MANUAL

    # passa do timeout de perda de alvo: e aqui que o vigia vazado agiria
    await asyncio.sleep(CONFIG_NAVIGATION["follow_lost_timeout_s"] + 0.5)
    await cenario.bus.aguardar_fila_vazia()

    assert vigia.done()
    assert cenario.nav._tarefa_follow is None
    # o modo continua sendo do operador, e ninguem anunciou perda de alvo
    assert cenario.nav.modo is ModoNavegacao.MANUAL
    assert not any(e.topico == "navigation.target_lost" for e in cenario.eventos)


@pytest.mark.asyncio
async def test_follow_perdendo_alvo_ainda_sai_para_hold(cenario):
    """Contraprova do teste acima: o cancelamento do vigia ao trocar de modo
    nao pode quebrar o caminho em que o PROPRIO vigia troca o modo. Ele chama
    _mudar_modo(HOLD) de dentro de si - cancelar a si mesmo ali levantaria
    CancelledError no meio do publish e o mode_changed nunca sairia."""
    await cenario.nav.iniciar_follow()
    await asyncio.sleep(CONFIG_NAVIGATION["follow_lost_timeout_s"] + 0.5)
    await cenario.bus.aguardar_fila_vazia()

    assert cenario.nav.modo is ModoNavegacao.HOLD
    assert any(e.topico == "navigation.target_lost" for e in cenario.eventos)
    modos = [e.dados["modo"] for e in cenario.eventos if e.topico == "navigation.mode_changed"]
    assert "HOLD" in modos  # o anuncio da troca sobreviveu


# ---------- GIRAR: a primitiva de manobra do desvio de obstaculo ----------


@pytest.mark.asyncio
async def test_girar_direita_manda_turn_right_com_graus(cenario):
    await cenario.bus.publish(
        "navigation.comando", {"acao": "GIRAR", "graus": 30.0, "sentido": "DIREITA"}
    )
    await cenario.bus.aguardar_fila_vazia()
    await asyncio.sleep(0.1)  # a execucao roda fora do bus (ultimo vence)

    assert "TURN_RIGHT" in cenario.comandos_enviados()
    enviado = Mensagem.from_bytes(cenario.transporte.enviados[-1])
    assert enviado.payload["graus"] == 30.0
    # graus PRESENTE e essencial: sem ele o firmware cai em girarContinuo, que
    # so para no timeout de 5s do Mega - um giro de fuga tem que terminar
    # sozinho para o robo poder reavaliar a frente.
    assert enviado.payload["velocidade_percent"] == CONFIG_MOTION["max_speed_percent"]


@pytest.mark.asyncio
async def test_girar_esquerda_manda_turn_left(cenario):
    await cenario.nav.girar(graus=45.0, sentido="ESQUERDA")

    assert "TURN_LEFT" in cenario.comandos_enviados()
    assert Mensagem.from_bytes(cenario.transporte.enviados[-1]).payload["graus"] == 45.0


@pytest.mark.asyncio
async def test_girar_nao_troca_o_modo_de_navegacao(cenario):
    """Desviar de obstaculo acontece DENTRO do modo que estiver valendo.

    Se GIRAR mudasse o modo, uma patrulha interrompida por um obstaculo
    voltaria como MANUAL e a missao morreria no primeiro movel da sala.
    """
    await cenario.nav.executar_patrol([{"graus": 0.0, "distancia_cm": 10.0}])
    modo_antes = cenario.nav.modo

    await cenario.nav.girar(graus=30.0, sentido="DIREITA")

    assert cenario.nav.modo is modo_antes
    assert not any(
        e.topico == "navigation.mode_changed" and e.dados["modo"] == "MANUAL"
        for e in cenario.eventos
    )


@pytest.mark.asyncio
async def test_girar_com_sentido_invalido_publica_erro(cenario):
    await cenario.bus.publish(
        "navigation.comando", {"acao": "GIRAR", "graus": 30.0, "sentido": "PRA_CIMA"}
    )
    await cenario.bus.aguardar_fila_vazia()
    await asyncio.sleep(0.1)

    erros = [e for e in cenario.eventos if e.topico == "navigation.error"]
    assert erros, "sentido invalido passou em silencio"
    assert "PRA_CIMA" in erros[-1].dados["motivo"]
    assert "TURN_LEFT" not in cenario.comandos_enviados()
    assert "TURN_RIGHT" not in cenario.comandos_enviados()


# ---------- "nao medi" nunca pode chegar como "esta livre" ----------


async def _telemetria(cenario, **campos):
    """Injeta um pacote de telemetria cru, com os campos que o teste quiser."""
    telemetria = Mensagem.nova(
        TipoMensagem.TELEMETRY, "hardware_core", "motion_core", campos
    )
    await cenario.bus.publish("comm.mensagem.telemetry", telemetria.to_dict())
    await cenario.bus.aguardar_fila_vazia()


@pytest.mark.asyncio
async def test_sem_eco_vira_desconhecido_e_nao_distancia(cenario):
    """REGRESSAO do fail-open achado em 2026-07-29.

    Quando nada reflete, o firmware manda o TETO DE ALCANCE (517cm) com
    `distancia_frontal_valida: true` e `frontal_sem_eco: true`. Ate esta data
    o NavigationCore engolia o 517 como parede medida a 5 metros - ou seja,
    caminho livre - e isso acontece MAIS quando o obstaculo esta PERTO (eco
    curto cabe inteiro dentro da cegueira do loop).
    """
    await cenario.injetar_lidar(None)

    assert cenario.nav.frente_desconhecida()
    assert cenario.nav._distancia_frontal_atual_cm is None


@pytest.mark.asyncio
async def test_leitura_de_verdade_continua_valendo(cenario):
    """Contraprova: sem esta, o teste acima passaria com um `return` que
    descartasse toda varredura - uma sonda que nunca diz sim."""
    await cenario.injetar_lidar(42.0)

    assert not cenario.nav.frente_desconhecida()
    assert cenario.nav._distancia_frontal_atual_cm == 42.0


@pytest.mark.asyncio
async def test_sem_eco_apaga_a_leitura_boa_anterior(cenario):
    """Perder a medida tem que APAGAR a anterior, nao mante-la.

    Segurar o ultimo valor bom seria pior que o bug original: o robo andaria
    apoiado numa medida velha achando que e atual, e sem nada na tela dizendo
    que a informacao envelheceu.

    VALE AINDA MAIS COM O LIDAR (2026-09-19). O ultrassom perdia o eco e
    voltava no ciclo seguinte; o LiDAR pode ficar com um setor mudo por muito
    tempo - foi o que aconteceu no dia da troca, com o setor de 150° sem eco
    em toda varredura. Se a leitura velha sobrevivesse, o robo andaria por
    minutos apoiado numa medida de antes.
    """
    await cenario.injetar_lidar(30.0)
    assert cenario.nav._distancia_frontal_atual_cm == 30.0

    await cenario.injetar_lidar(None)

    assert cenario.nav.frente_desconhecida()


@pytest.mark.asyncio
async def test_estado_do_mega_vem_tambem_da_telemetria(cenario):
    """REGRESSAO: `motion.status` so e publicado na BORDA (o firmware compara
    com o estado anterior). Depois de um restart do Motion Core, o Pi ficava
    com o `"IDLE"` inicial - um valor inventado - ate o Mega mudar de estado
    sozinho. Com o Mega parado em OBSTACLE_DETECTED nao ha transicao nenhuma
    a anunciar, e o Pi acreditava em IDLE indefinidamente enquanto o dado
    certo chegava na telemetria a cada segundo."""
    assert cenario.nav._estado_hardware == "IDLE"  # o valor inventado do boot

    await _telemetria(cenario, estado="OBSTACLE_DETECTED")

    assert cenario.nav._estado_hardware == "OBSTACLE_DETECTED"


# ---------------------------------------------------------------------------
# DUVIDA VIRA ESTUDO DE RISCO, NUNCA TRAVA (2026-07-30)
#
# Decisao do dono, depois de ver o risco exposto: *"o robo precisa ter mais
# iniciativa e coragem, travas de seguranca so impedem o robo de andar -
# retira as travas, temos os sensores para realizar estudos de risco e tomada
# de decisao"*. Mesma linha do EDR-0027, agora aplicada ao Pi.
#
# O que os testes abaixo fixam: o robo OLHA (a varredura e o estudo), ANDA de
# qualquer jeito, e o que ele descobriu vira dado para quem decide. Andar sem
# saber e escolha do dono; andar sem saber e sem REGISTRAR seria defeito.
#
# EXCECAO DESDE 2026-08-18 ("manual obedece 100%"): este estudo mora so no
# caminho AUTONOMO (`executar_goto`/PATROL). No manual o operador manda e o
# robo obedece sem olhar - quem estuda o risco e o humano dirigindo.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frente_desconhecida_nao_impede_o_avanco(cenario):
    """A trava saiu: nem o "nao sei" segura o robo."""
    cenario.definir_leituras_scan([])  # olhou e nada refletiu
    await cenario.injetar_lidar(None)

    await cenario.nav.executar_manual("MOVE_FORWARD")
    await cenario.bus.aguardar_fila_vazia()

    assert "MOVE_FORWARD" in cenario.comandos_enviados()


@pytest.mark.asyncio
async def test_olha_antes_de_avancar_quando_nao_sabe(cenario):
    """O estudo de risco acontece: 7 angulos medidos onde havia so um None.

    Vive no caminho AUTONOMO (`executar_goto`): desde 2026-08-18 o manual
    obedece 100% e nao estuda nada - quem dirige e o operador."""
    cenario.definir_leituras_scan([])
    await cenario.injetar_lidar(None)

    await cenario.nav.executar_goto(graus=0, distancia_cm=50)
    await cenario.bus.aguardar_fila_vazia()

    assert "SCAN_FRONT" in cenario.comandos_enviados()


@pytest.mark.asyncio
async def test_risco_assumido_e_declarado_para_quem_decide(cenario):
    """Andar as cegas tem que DEIXAR RASTRO.

    Sem este evento o robo andaria sem medida e nada no sistema saberia -
    nem a IA, nem o diario, nem a interface. O dado e o que torna a decisao
    possivel; sem ele, "tomada de decisao" seria so uma frase. (Caminho
    AUTONOMO: o manual obedece 100% e nao estuda risco desde 2026-08-18.)
    """
    cenario.definir_leituras_scan([])
    await cenario.injetar_lidar(None)

    await cenario.nav.executar_goto(graus=0, distancia_cm=50)
    await cenario.bus.aguardar_fila_vazia()

    riscos = [e for e in cenario.eventos if e.topico == "navigation.risco_assumido"]
    assert riscos, "andou sem medida e nao contou a ninguem"
    assert riscos[-1].dados["medida_confiavel"] is False
    assert riscos[-1].dados["motivo"] == "frente_desconhecida"


@pytest.mark.asyncio
async def test_varredura_que_mede_espaco_nao_declara_risco(cenario):
    """CONTRAPROVA: com medida boa, nao ha risco a declarar.

    Sem este teste, o de cima passaria com um publish incondicional - e o
    evento viraria ruido que se aprende a ignorar. (Caminho AUTONOMO: o
    manual obedece 100% e nao declara risco desde 2026-08-18.)
    """
    cenario.definir_leituras_scan(
        [
            {"angulo": 60, "distancia_cm": 120.0, "valida": True},
            {"angulo": 90, "distancia_cm": 150.0, "valida": True},
            {"angulo": 120, "distancia_cm": 130.0, "valida": True},
        ]
    )
    await cenario.injetar_lidar(None)

    await cenario.nav.executar_goto(graus=0, distancia_cm=50)
    await cenario.bus.aguardar_fila_vazia()

    assert "MOVE_DISTANCE" in cenario.comandos_enviados()
    assert not [e for e in cenario.eventos if e.topico == "navigation.risco_assumido"]


@pytest.mark.asyncio
async def test_obstaculo_medido_continua_reportando_obstaculo(cenario):
    """A informacao que RESTOU: obstaculo medido de verdade.

    Com "manual obedece 100%" (dono, 2026-08-18) o avanco nao e mais vetado
    aqui, mas o obstaculo continua sendo REPORTADO - evento no mesmo topico,
    com o motivo no payload, para o dashboard e a IA saberem que o operador
    dirigiu para cima de um obstaculo.
    """
    await cenario.injetar_lidar(10.0)

    await cenario.nav.executar_manual("MOVE_FORWARD")
    await cenario.bus.aguardar_fila_vazia()

    assert "MOVE_FORWARD" in cenario.comandos_enviados()
    recusas = [e for e in cenario.eventos if e.topico == "navigation.obstacle_avoided"]
    assert recusas[-1].dados["motivo"] == "obstaculo_frontal"


@pytest.mark.asyncio
async def test_segmento_so_se_declara_completo_depois_do_movimento(cenario):
    """`segment_completed` saía no instante em que o movimento MAL COMEÇAVA.

    O `send` garante o ACK, e o ACK diz que o comando CHEGOU - não que o robô
    andou (o firmware executa MOVE_DISTANCE de forma assíncrona, ver
    INTERVALO_CHECAGEM_OCIOSO_S). O evento afirmava um segmento pronto que ainda
    não existia; quem consome pela palavra - diário, interface, quem for medir
    trecho percorrido - lia errado.

    O firmware é imitado no ponto exato em que ele age: ao RECEBER o
    MOVE_DISTANCE o corpo passa a EXECUTING_MISSION, como o
    `command_executor.h` faz. Sem isso o teste passaria dos dois jeitos - a
    espera do início do segmento comeria o tempo todo e nada seria provado.
    """
    nav = cenario.nav
    send_original = nav._comm.send

    async def send_que_ocupa_o_corpo(destino, payload, *args, **kwargs):
        resultado = await send_original(destino, payload, *args, **kwargs)
        if payload.get("comando") == "MOVE_DISTANCE":
            nav._estado_hardware = "EXECUTING_MISSION"
        return resultado

    nav._comm.send = send_que_ocupa_o_corpo

    tarefa = asyncio.create_task(
        nav._executar_segmento(graus=0.0, distancia_cm=30.0, velocidade_percent=None)
    )
    # tempo de sobra para o MOVE_DISTANCE sair e o evento sair junto, se saísse
    await asyncio.sleep(0.4)

    assert "MOVE_DISTANCE" in cenario.comandos_enviados(), "o segmento nem chegou a andar"
    concluidos = [e for e in cenario.eventos if e.topico == "navigation.segment_completed"]
    assert concluidos == [], "declarou o segmento completo com o robô ainda em movimento"

    nav._estado_hardware = "IDLE"  # o corpo terminou de andar
    assert await asyncio.wait_for(tarefa, timeout=2.0) is True

    concluidos = [e for e in cenario.eventos if e.topico == "navigation.segment_completed"]
    assert len(concluidos) == 1


@pytest.mark.asyncio
async def test_lidar_que_falha_apaga_a_medida_e_marca_o_sensor(cenario):
    """`lidar.error` tem que derrubar a medida, nao so aparecer no log.

    O topico era ORFAO ate 2026-09-19: o gerenciador publicava e ninguem
    escutava. Enquanto o ultrassom existia isso era barato; com o LiDAR como
    unico sensor de distancia, ignorar a falha dele significa o robo andar
    apoiado numa varredura de quando o sensor ainda girava.
    """
    await cenario.injetar_lidar(40.0)
    assert cenario.nav._distancia_frontal_atual_cm == 40.0
    assert cenario.nav._frontal_sensor_ok is True

    await cenario.bus.publish("lidar.error", {"motivo": "porta sumiu"})
    await cenario.bus.aguardar_fila_vazia()

    assert cenario.nav.frente_desconhecida()
    assert cenario.nav._distancia_frontal_atual_cm is None
    # False, e nao None: "ja falou comigo e parou" e diferente de "nunca falou"
    assert cenario.nav._frontal_sensor_ok is False


@pytest.mark.asyncio
async def test_varredura_sem_eco_mantem_o_sensor_vivo(cenario):
    """Frente sem eco NAO e sensor morto - e sensor vivo que nao mediu.

    Essa diferenca decide se o robo faz o ESTUDO DE RISCO (varredura +
    `navigation.risco_assumido`) ou so anda avisando no log. Quando a
    distancia deixou de vir do ultrassom, `_frontal_sensor_ok` ficou sem
    ninguem para preencher e o estudo de risco sumiria em silencio.
    """
    await cenario.injetar_lidar(None)

    assert cenario.nav.frente_desconhecida()
    assert cenario.nav._frontal_sensor_ok is True
