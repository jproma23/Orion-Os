"""PARAR DE VERDADE (2026-11-10): as provas da auditoria viraram testes.

Cada teste monta a cadeia REAL do Pi - EventBus + ComunicacaoService +
NavigationCore + TravaLidar como porteiro + o gancho do STOP repassado, como o
motion_core/__main__.py liga - com um link "mission_core" (Notebook) e um
"hardware_core" (ESP32) falsos. O ESP32 falso ACKa e anota cada COMMAND.

O criterio e o do corpo: depois do STOP, NENHUM comando de roda (CMD_VEL,
MOVE_DISTANCE, TURN_*) chega ao ESP32 por 1 s. Achados: C1-01, C1-02/C5-01,
C1-03, C1-06/A3, C5-02, C5-03, gesto "vem" (C1-05), A5 e a decisao do dono
"STOP em AUTONOMO passa para MANUAL".
"""
import asyncio
import time

import pytest
from conftest import FakeTransporte

from motion_core.behavior.behavior_core import MODO_AUTONOMO, MODO_MANUAL, BehaviorCore
from motion_core.behavior.comportamento import AUTORIDADE_AUTONOMA, Comportamento
from motion_core.behavior.comportamentos import AtenderGesto, Repouso
from motion_core.navigation import nav2_goal as modulo_nav2_goal
from motion_core.navigation import navigation_core as nc
from motion_core.navigation.nav2_goal import Nav2Goal
from motion_core.navigation.navigation_core import ModoNavegacao, NavigationCore
from motion_core.navigation.trava_lidar import TravaLidar
from orion.communication.protocol import ErroProtocoloInvalido, Mensagem, TipoMensagem
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus
from orion.mission import mission_planner as mp

CONF_M = {"max_speed_percent": 60, "min_front_distance_cm": 25, "mission_timeout_s": 30}
CONF_N = {
    "patrol_scan_before_segment": True,
    "obstacle_retry_max": 2,
    "follow_lost_timeout_s": 30,
    "reduced_speed_near_person_percent": 30,
}
COMANDOS_DE_RODA = {
    "CMD_VEL", "MOVE_DISTANCE", "MOVE_FORWARD", "MOVE_CONTINUOUS", "TURN_LEFT", "TURN_RIGHT"
}
PESSOA = {"classe": "person", "caixa": (280, 100, 360, 400)}


def _comando_do_notebook(comando: str) -> Mensagem:
    return Mensagem.nova(
        TipoMensagem.COMMAND, "mission_core", "hardware_core", {"comando": comando}
    )


class Cadeia:
    """A cadeia do Pi com corpo e Notebook falsos (base: provas/c1_*.py)."""

    def __init__(self, atraso_ack_s: float = 0.0, duracao_movimento_s: float = 0.0) -> None:
        self.bus = EventBus()
        self.comm = ComunicacaoService("motion_core", self.bus, max_retries=2, ack_timeout_ms=300)
        self.esp = FakeTransporte()
        self.note = FakeTransporte()
        self.comm.adicionar_link("hardware_core", self.esp, exigir_checksum_mensagem=False)
        self.comm.adicionar_link("mission_core", self.note)
        self.nav = NavigationCore(self.bus, self.comm, CONF_M, CONF_N)
        self.trava = TravaLidar(self.bus, corpo_frente_cm=8, corpo_tras_cm=22, cegas_para_parar=2)
        self.comm.definir_porteiro("hardware_core", self.trava.motivo_recusa)
        # O MESMO fio do motion_core/__main__.py.
        self.comm.definir_gancho_stop(
            "hardware_core", lambda _m: self.nav.parar_ja("notebook")
        )
        self.atraso = atraso_ack_s
        self.duracao_movimento_s = duracao_movimento_s
        self._fim_movimento: float | None = None
        self.t0 = time.monotonic()
        #: (instante, comando, payload) de cada COMMAND que chegou ao ESP32.
        self.log: list[tuple[float, str, dict]] = []
        self._tarefas: list[asyncio.Task] = []

    async def iniciar(self) -> None:
        self._tarefas = [
            asyncio.create_task(self.bus.iniciar()),
            asyncio.create_task(self._esp32()),
            asyncio.create_task(self._lidar()),
            asyncio.create_task(self._status()),
        ]
        await asyncio.sleep(0.25)  # primeira varredura do LiDAR chegar

    async def _lidar(self) -> None:
        # Sala livre a 300 cm, pessoa a 200 cm na frente; 5 Hz como o real.
        while True:
            raios = [300.0] * 360
            for angulo in range(80, 101):
                raios[angulo] = 200.0
            await self.bus.publish("lidar.scan", {"raios_cm": raios})
            await asyncio.sleep(0.2)

    async def _status(self) -> None:
        while True:
            andando = self._fim_movimento is not None and time.monotonic() < self._fim_movimento
            await self.bus.publish(
                "motion.status", {"estado": "EXECUTING_MISSION" if andando else "IDLE"}
            )
            await asyncio.sleep(0.1)

    async def _esp32(self) -> None:
        indice = 0
        while True:
            while indice >= len(self.esp.enviados):
                await asyncio.sleep(0.002)
            m = Mensagem.from_bytes(self.esp.enviados[indice])
            indice += 1
            if m.tipo is not TipoMensagem.COMMAND:
                continue
            comando = m.payload.get("comando")
            self.log.append((time.monotonic(), comando, dict(m.payload)))
            if comando in ("MOVE_DISTANCE", "TURN_LEFT", "TURN_RIGHT") and self.duracao_movimento_s:
                self._fim_movimento = time.monotonic() + self.duracao_movimento_s
            elif comando == "STOP":
                self._fim_movimento = None
            if self.atraso:
                await asyncio.sleep(self.atraso)
            await self.esp.injetar(Mensagem.ack(m, origem="hardware_core").to_bytes())

    def primeiro_stop_apos(self, instante: float) -> float | None:
        for t, comando, _ in self.log:
            if comando == "STOP" and t >= instante:
                return t
        return None

    def rodas_apos(self, instante: float) -> list[tuple[float, str, dict]]:
        return [linha for linha in self.log if linha[0] > instante and linha[1] in COMANDOS_DE_RODA]

    async def encerrar(self) -> None:
        self.nav._segurar_tudo()
        for tarefa in self._tarefas:
            tarefa.cancel()
        for tarefa in self._tarefas:
            try:
                await tarefa
            except asyncio.CancelledError:
                pass


@pytest.fixture
def sem_rede_nav2(monkeypatch):
    """Nav2Goal sem HTTP: anota os /cancelar e responde goal 'completado'."""
    chamadas = {"cancelar": 0, "goal": 0}

    def cancelar(self):
        chamadas["cancelar"] += 1

    def enviar(self, payload):
        chamadas["goal"] += 1
        return "completado", 200

    monkeypatch.setattr(Nav2Goal, "_enviar_cancelar", cancelar)
    monkeypatch.setattr(Nav2Goal, "_enviar", enviar)
    return chamadas


# ---------- C1-02 / C5-01: "Jarvis, para" vindo do Notebook ----------


@pytest.mark.asyncio
async def test_stop_falado_do_notebook_para_o_follow_de_verdade():
    """Prova c1_stop_nao_gruda.py, cenario A.

    Antes: o STOP so era repassado ao corpo; o FOLLOW nem sabia e mandava
    CMD_VEL de novo em <=200 ms, com a camera ainda vendo a pessoa.
    """
    c = Cadeia()
    await c.iniciar()
    try:
        await c.nav.iniciar_follow()
        await c.bus.publish("motion.pan_tilt", {"pan": 0.0, "tilt": 0})
        for _ in range(6):
            await c.bus.publish("vision.person_detected", PESSOA)
            await asyncio.sleep(0.15)
        assert any(cmd == "CMD_VEL" for _, cmd, _ in c.log), "o FOLLOW nem chegou a andar"

        stop = _comando_do_notebook("STOP")
        t_stop = time.monotonic()
        await c.note.injetar(stop.to_bytes())
        for _ in range(7):  # a camera continua vendo a pessoa por 1 s
            await c.bus.publish("vision.person_detected", PESSOA)
            await asyncio.sleep(0.15)

        assert c.primeiro_stop_apos(t_stop) is not None
        assert c.rodas_apos(t_stop) == [], f"o FOLLOW voltou a andar: {c.rodas_apos(t_stop)}"
        assert c.nav.modo is ModoNavegacao.HOLD
    finally:
        await c.encerrar()


@pytest.mark.asyncio
async def test_stop_falado_em_autonomo_passa_o_volante_para_manual(sem_rede_nav2):
    """Decisao do dono (10/11): STOP de gente em AUTONOMO tira o volante da IA."""
    c = Cadeia()
    maestro = BehaviorCore(c.bus)
    Nav2Goal(c.bus)
    await c.iniciar()
    try:
        assert maestro.modo_controle == MODO_AUTONOMO
        stop = _comando_do_notebook("STOP")
        await c.note.injetar(stop.to_bytes())
        await asyncio.sleep(0.2)
        await c.bus.aguardar_fila_vazia()
        assert maestro.modo_controle == MODO_MANUAL
        assert sem_rede_nav2["cancelar"] == 1, "o STOP falado tem que cancelar o goal do Nav2"
    finally:
        await c.encerrar()


@pytest.mark.asyncio
async def test_hold_do_proprio_robo_nao_tira_o_volante_da_ia(sem_rede_nav2):
    """Contraprova: HOLD dos comportamentos (atender, joystick solto, a acao
    "parar" da IA) para as rodas mas NAO e gente pedindo - o volante fica."""
    c = Cadeia()
    maestro = BehaviorCore(c.bus)
    Nav2Goal(c.bus)
    await c.iniciar()
    try:
        t = time.monotonic()
        await c.bus.publish("navigation.comando", {"acao": "HOLD"})
        await asyncio.sleep(0.2)
        await c.bus.aguardar_fila_vazia()
        assert c.primeiro_stop_apos(t) is not None
        assert maestro.modo_controle == MODO_AUTONOMO
        assert sem_rede_nav2["cancelar"] == 1  # o Nav2 para em qualquer STOP
    finally:
        await c.encerrar()


# ---------- C1-01: STOP da webui com o Nav2 dirigindo ----------


@pytest.mark.asyncio
async def test_stop_da_webui_cancela_o_nav2_e_trava_a_velocidade(sem_rede_nav2):
    """Prova c1_stop_nao_gruda.py, cenario B.

    Antes: o bridge_cmd_vel voltava a mandar CMD_VEL em <=100 ms (o goal nao
    era cancelado; URL_CANCELAR era orfa).
    """
    c = Cadeia()
    Nav2Goal(c.bus)
    await c.iniciar()
    parar = asyncio.Event()

    async def nav2() -> None:
        while not parar.is_set():
            await c.bus.publish(
                "navigation.comando", {"acao": "VELOCIDADE", "v_m_s": 0.11, "w_rad_s": 0.0}
            )
            await asyncio.sleep(0.1)

    tarefa = asyncio.create_task(nav2())
    try:
        await c.bus.publish("behavior.modo_controle", {"modo": "manual"})
        await asyncio.sleep(0.5)
        assert any(cmd == "CMD_VEL" for _, cmd, _ in c.log), "o Nav2 nem chegou a andar"

        t_stop = time.monotonic()
        await c.bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "STOP"})
        await asyncio.sleep(1.0)

        stop = c.primeiro_stop_apos(t_stop)
        assert stop is not None and stop - t_stop < 0.1
        assert c.rodas_apos(t_stop) == [], f"o Nav2 voltou a andar: {c.rodas_apos(t_stop)}"
        assert sem_rede_nav2["cancelar"] == 1
        assert c.nav.velocidade_travada

        # Goal NOVO = pedido explicito: o Nav2 volta a dirigir.
        await c.bus.publish("navigation.goal", {"x_m": 1.0, "y_m": 0.0})
        t_goal = time.monotonic()
        await asyncio.sleep(0.4)
        assert not c.nav.velocidade_travada
        assert any(cmd == "CMD_VEL" for _, cmd, _ in c.rodas_apos(t_goal))
    finally:
        parar.set()
        await tarefa
        await c.encerrar()


@pytest.mark.asyncio
async def test_velocidade_nao_apaga_o_stop_na_vaga():
    """Prova c1_stop_nao_gruda.py, cenario C: VELOCIDADE logo depois do STOP
    o sobrescrevia na vaga unica (20 STOP publicados, 0 entregues)."""
    c = Cadeia(atraso_ack_s=0.06)
    await c.iniciar()
    try:
        tentativas = 20
        entregues = 0
        for _ in range(tentativas):
            await c.bus.publish(
                "navigation.comando", {"acao": "VELOCIDADE", "v_m_s": 0.11, "w_rad_s": 0.0}
            )
            await asyncio.sleep(0.01)
            antes = sum(1 for _, cmd, _ in c.log if cmd == "STOP")
            await c.bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "STOP"})
            await asyncio.sleep(0.01)
            await c.bus.publish(
                "navigation.comando", {"acao": "VELOCIDADE", "v_m_s": 0.11, "w_rad_s": 0.0}
            )
            await asyncio.sleep(0.25)
            if sum(1 for _, cmd, _ in c.log if cmd == "STOP") > antes:
                entregues += 1
        assert entregues == tentativas
        primeiro_stop = c.primeiro_stop_apos(0.0)
        assert c.rodas_apos(primeiro_stop) == []
    finally:
        await c.encerrar()


@pytest.mark.asyncio
async def test_definir_velocidade_direto_tambem_recusa_com_a_trava():
    c = Cadeia()
    await c.iniciar()
    try:
        await c.nav.parar_tudo("operador")
        t = time.monotonic()
        await c.nav.definir_velocidade(0.1, 0.0)
        await asyncio.sleep(0.05)
        assert c.rodas_apos(t) == []
    finally:
        await c.encerrar()


# ---------- C1-03: STOP durante o EXPLORE ----------


@pytest.mark.asyncio
async def test_stop_da_webui_no_explore_chega_na_hora_e_nada_mais_anda():
    """Prova c1_stop_no_explore.py: o STOP esperava o passo de 4 s acabar
    (apertado em 1,03 s, chegou em 4,53 s) e o trecho seguia."""
    c = Cadeia(duracao_movimento_s=4.0)
    await c.iniciar()
    try:
        await c.bus.publish("navigation.comando", {"acao": "EXPLORE"})
        for _ in range(100):
            if any(cmd == "MOVE_DISTANCE" for _, cmd, _ in c.log):
                break
            await asyncio.sleep(0.05)
        assert any(cmd == "MOVE_DISTANCE" for _, cmd, _ in c.log)
        await asyncio.sleep(0.5)

        t_stop = time.monotonic()
        await c.bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "STOP"})
        await asyncio.sleep(1.0)

        stop = c.primeiro_stop_apos(t_stop)
        assert stop is not None and stop - t_stop < 0.1, "o STOP esperou o passo acabar"
        assert c.rodas_apos(t_stop) == [], f"o EXPLORE seguiu: {c.rodas_apos(t_stop)}"
        assert c.nav.modo is ModoNavegacao.HOLD
    finally:
        await c.encerrar()


class _IaQueExplora(Comportamento):
    """Imita a IA estrategica: com o volante, manda EXPLORE de novo."""

    nome = "ia_falsa"
    prioridade = 40
    autoridade = AUTORIDADE_AUTONOMA

    def quer_rodar(self) -> bool:
        return True

    async def executar(self) -> None:
        await self._event_bus.publish("navigation.comando", {"acao": "EXPLORE"})
        while True:
            await asyncio.sleep(1.0)


@pytest.mark.asyncio
async def test_stop_no_explore_em_autonomo_a_ia_nao_manda_explorar_de_novo(sem_rede_nav2):
    """Prova v_stop_explore_sobrescrito.py: em AUTONOMO a IA mandava outro
    EXPLORE logo depois e o robo saia andando. Agora o STOP do operador passa
    o volante para MANUAL e a IA perde as rodas."""
    c = Cadeia(duracao_movimento_s=4.0)
    maestro = BehaviorCore(c.bus)
    maestro.registrar(Repouso(c.bus))
    maestro.registrar(_IaQueExplora(c.bus))
    Nav2Goal(c.bus)
    await c.iniciar()
    laco = asyncio.create_task(maestro.executar())
    try:
        for _ in range(100):
            if any(cmd == "MOVE_DISTANCE" for _, cmd, _ in c.log):
                break
            await asyncio.sleep(0.05)
        assert any(cmd == "MOVE_DISTANCE" for _, cmd, _ in c.log)
        await asyncio.sleep(0.5)

        t_stop = time.monotonic()
        await c.bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "STOP"})
        await asyncio.sleep(1.0)

        assert maestro.modo_controle == MODO_MANUAL
        assert c.rodas_apos(t_stop) == [], f"a IA mandou andar de novo: {c.rodas_apos(t_stop)}"
    finally:
        maestro.parar()
        await laco
        await c.encerrar()


# ---------- A5: o resultado do goal tem quem guarde ----------


@pytest.mark.asyncio
async def test_resultado_do_goal_fica_guardado_na_navegacao(sem_rede_nav2):
    c = Cadeia()
    Nav2Goal(c.bus)
    await c.iniciar()
    try:
        assert c.nav.ultimo_goal_resultado is None
        await c.bus.publish("navigation.goal", {"x_m": 1.0, "y_m": 2.0})
        await asyncio.sleep(0.2)
        await c.bus.aguardar_fila_vazia()
        resultado = c.nav.ultimo_goal_resultado
        assert resultado is not None and resultado["resultado"] == "completado"
        assert resultado["x_m"] == 1.0 and "instante" in resultado
    finally:
        await c.encerrar()


@pytest.mark.asyncio
async def test_cancelar_com_nav2_desligado_so_loga(monkeypatch):
    """Nav2 sob demanda costuma estar desligado: cancelar falha e nada quebra."""

    def recusado(self):
        raise ConnectionRefusedError("nav2 fora")

    monkeypatch.setattr(Nav2Goal, "_enviar_cancelar", recusado)
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    Nav2Goal(bus)
    await bus.publish("navigation.parar", {"origem": "operador", "humana": True})
    await bus.aguardar_fila_vazia()
    await asyncio.sleep(0.1)
    bus.parar()
    tarefa_bus.cancel()
    try:
        await tarefa_bus
    except asyncio.CancelledError:
        pass


def test_cancelar_vai_na_url_certa(monkeypatch):
    pedidos = []

    class _Resposta:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"resultado": "cancelado"}'

    def urlopen(req, timeout):
        pedidos.append((req.full_url, req.get_method(), timeout))
        return _Resposta()

    monkeypatch.setattr(modulo_nav2_goal.urllib.request, "urlopen", urlopen)
    Nav2Goal.__new__(Nav2Goal)._enviar_cancelar()
    assert pedidos == [(modulo_nav2_goal.URL_CANCELAR, "POST", modulo_nav2_goal.TIMEOUT_CANCELAR_S)]


# ---------- C1-06 / A3: mensagem ruim nao mata a recepcao do link ----------


async def _link_com_porteiro(porteiro):
    bus = EventBus()
    comm = ComunicacaoService("motion_core", bus)
    note, esp = FakeTransporte(), FakeTransporte()
    comm.adicionar_link("mission_core", note)
    comm.adicionar_link("hardware_core", esp)
    comm.definir_porteiro("hardware_core", porteiro)
    return comm, note, esp


@pytest.mark.asyncio
async def test_porteiro_que_quebra_vira_nack_e_o_stop_seguinte_chega():
    """Prova c1_porteiro_excecao.py: `"distancia_cm": "20 cm"` fazia o
    porteiro levantar ValueError e a recepcao do Notebook morria calada."""

    def porteiro(payload):
        if payload.get("comando") == "MOVE_DISTANCE":
            float(payload["distancia_cm"])  # "20 cm" -> ValueError
        return None

    comm, note, esp = await _link_com_porteiro(porteiro)
    ruim = Mensagem.nova(
        TipoMensagem.COMMAND, "mission_core", "hardware_core",
        {"comando": "MOVE_DISTANCE", "distancia_cm": "20 cm"},
    )
    await note.injetar(ruim.to_bytes())
    await asyncio.sleep(0.05)

    assert not comm._tarefas_recepcao["mission_core"].done(), "a recepcao do Notebook morreu"
    assert esp.enviados == []  # o comando ruim nao andou
    nacks = [Mensagem.from_bytes(b) for b in note.enviados]
    assert any(
        m.tipo is TipoMensagem.NACK and m.payload.get("motivo") == "payload invalido" for m in nacks
    )

    bom = _comando_do_notebook("STOP")
    await note.injetar(bom.to_bytes())
    await asyncio.sleep(0.05)
    assert [Mensagem.from_bytes(b).payload["comando"] for b in esp.enviados] == ["STOP"]
    await comm.encerrar()


@pytest.mark.asyncio
async def test_porteiro_que_quebra_no_send_vira_erro_de_comunicacao():
    from orion.communication.service import ErroComunicacao

    def porteiro(payload):
        raise ValueError("payload torto")

    comm, _note, _esp = await _link_com_porteiro(porteiro)
    with pytest.raises(ErroComunicacao, match="payload invalido"):
        await comm.send("hardware_core", {"comando": "MOVE_DISTANCE"})
    await comm.encerrar()


def test_bytes_que_nao_sao_utf8_viram_erro_de_protocolo():
    with pytest.raises(ErroProtocoloInvalido):
        Mensagem.from_bytes(b"\xff\xfe{")


@pytest.mark.asyncio
async def test_byte_invalido_nao_derruba_a_recepcao():
    comm, note, esp = await _link_com_porteiro(lambda payload: None)
    await note.injetar(b"\xff\xfe\x00 lixo")
    await asyncio.sleep(0.05)
    assert not comm._tarefas_recepcao["mission_core"].done()
    bom = _comando_do_notebook("STOP")
    await note.injetar(bom.to_bytes())
    await asyncio.sleep(0.05)
    assert len(esp.enviados) == 1
    await comm.encerrar()


@pytest.mark.asyncio
async def test_excecao_qualquer_ao_tratar_mensagem_nao_mata_a_recepcao(monkeypatch):
    comm, note, esp = await _link_com_porteiro(lambda payload: None)
    original = comm._tratar_mensagem_recebida
    vezes = {"n": 0}

    async def as_vezes_quebra(nome_peer, transporte, mensagem):
        vezes["n"] += 1
        if vezes["n"] == 1:
            raise RuntimeError("defeito qualquer num handler")
        await original(nome_peer, transporte, mensagem)

    monkeypatch.setattr(comm, "_tratar_mensagem_recebida", as_vezes_quebra)
    for _ in range(2):
        m = _comando_do_notebook("STOP")
        await note.injetar(m.to_bytes())
    await asyncio.sleep(0.05)
    assert not comm._tarefas_recepcao["mission_core"].done()
    assert len(esp.enviados) == 1  # a primeira quebrou, a segunda passou
    await comm.encerrar()


@pytest.mark.asyncio
async def test_gancho_do_stop_vale_sem_rota_e_nao_ve_outros_comandos():
    chamadas = []
    bus = EventBus()
    comm = ComunicacaoService("motion_core", bus)
    note = FakeTransporte()
    comm.adicionar_link("mission_core", note)  # SEM link com o corpo
    comm.definir_gancho_stop("hardware_core", lambda m: chamadas.append(m.payload["comando"]))
    for comando in ("LIGHT_ON", "STOP"):
        m = _comando_do_notebook(comando)
        await note.injetar(m.to_bytes())
    await asyncio.sleep(0.05)
    assert chamadas == ["STOP"]
    await comm.encerrar()


# ---------- C5-02: o STOP falado nao espera a memoria ----------


class _MemoriaLenta:
    """Imita o link de memoria caido (SSD solta): cada chamada leva 2 s."""

    def __init__(self) -> None:
        self.chamadas: list[str] = []

    async def _lenta(self, nome):
        self.chamadas.append(nome)
        await asyncio.sleep(2.0)
        raise TimeoutError("sem resposta")

    async def context(self, pessoa_id, sobre=None):
        return await self._lenta("context")

    async def nota_buscar(self, texto):
        return await self._lenta("nota_buscar")

    async def saber_buscar(self, texto):
        return await self._lenta("saber_buscar")

    async def remember(self, *args):
        return await self._lenta("remember")

    async def registrar_consulta_ia(self):
        return await self._lenta("registrar_consulta_ia")


class _IaQueNaoDeviaSerChamada:
    async def responder(self, texto, contexto=None, **kwargs):
        raise AssertionError("parar nao passa pela IA")

    async def responder_em_frases(self, texto, contexto=None, **kwargs):
        raise AssertionError("parar nao passa pela IA")
        yield  # pragma: no cover


@pytest.mark.asyncio
@pytest.mark.parametrize("caminho", ["processar", "responder_em_frases"])
async def test_stop_falado_sai_em_menos_de_50ms_com_a_memoria_lenta(caminho):
    """Prova c5_cascata.py: com a memoria sem responder, o STOP falado levava
    6,03 s - o `_pediu_para_parar` vinha DEPOIS do `_consultar_contexto`."""
    enviado_em = []

    async def hw(comando, parametros=None):
        enviado_em.append((comando, time.monotonic()))

    memoria = _MemoriaLenta()
    planner = mp.MissionPlanner(
        _IaQueNaoDeviaSerChamada(), enviar_comando_hardware=hw, memory_client=memoria
    )
    t0 = time.monotonic()
    if caminho == "processar":
        tarefa = asyncio.create_task(planner.processar("Fofao, para!"))
        primeira = None
    else:
        frases = planner.responder_em_frases("Fofao, para!")
        primeira = await frases.__anext__()
        tarefa = asyncio.create_task(frases.aclose())
    for _ in range(50):
        if enviado_em:
            break
        await asyncio.sleep(0.001)
    assert enviado_em and enviado_em[0][0] == "STOP"
    assert enviado_em[0][1] - t0 < 0.05, f"STOP levou {enviado_em[0][1] - t0:.2f} s"
    assert "context" not in memoria.chamadas and "nota_buscar" not in memoria.chamadas
    if primeira is not None:
        assert primeira == mp.RESPOSTA_PARAR
    tarefa.cancel()
    try:
        await tarefa
    except (asyncio.CancelledError, Exception):
        pass


# ---------- C4-01 / C5-03: "varre a frente" pela navegacao ----------


@pytest.mark.asyncio
async def test_varrer_a_frente_vai_pela_navegacao_e_nao_pelo_firmware():
    """O SCAN_FRONT cru ia ao ESP32, que nao o conhece e ACKa mesmo assim: o
    robo dizia "Varrendo a frente" e nada acontecia."""
    navegacao, corpo = [], []

    async def comandar_navegacao(dados):
        navegacao.append(dados)

    async def hw(comando, parametros=None):
        corpo.append(comando)

    planner = mp.MissionPlanner(
        _IaQueNaoDeviaSerChamada(),
        enviar_comando_hardware=hw,
        comandar_navegacao=comandar_navegacao,
    )
    await planner._cumprir_acao_pedida("#acao:varrer_a_frente Vou olhar.", "varre a frente")
    assert navegacao == [{"acao": "SCAN_FRONT"}]
    assert corpo == []


# ---------- gesto "vem": o MOVE_FORWARD sem fim tem que parar ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("preemptar", [False, True])
async def test_gesto_vem_manda_parar_no_fim(preemptar):
    """C1-05/C4-03: o "vem" mandava MOVE_FORWARD e nunca STOP; o homem-morto
    do firmware e renovado pelo SET_PAN_TILT da visao, entao nada parava."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(dict(e.dados)))
    gesto = AtenderGesto(bus, duracao_s=0.05 if not preemptar else 5.0)
    gesto._gesto = "vem"
    gesto._centro_x = 0.5
    tarefa = asyncio.create_task(gesto.executar())
    await asyncio.sleep(0.2 if not preemptar else 0.1)
    if preemptar:
        tarefa.cancel()
    try:
        await tarefa
    except asyncio.CancelledError:
        pass
    await bus.aguardar_fila_vazia()
    assert {"acao": "MANUAL", "comando": "MOVE_FORWARD"} in comandos
    assert comandos[-1] == {"acao": "HOLD", "origem": "gesto"}
    bus.parar()
    tarefa_bus.cancel()
    try:
        await tarefa_bus
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_parar_tudo_nao_cancela_a_si_mesmo():
    """Chamado de DENTRO da tarefa de comando (definir_modo_hold direto),
    cancelar a si mesmo mataria o STOP no meio."""
    c = Cadeia()
    await c.iniciar()
    try:
        async def dentro():
            c.nav._tarefa_comando = asyncio.current_task()
            await c.nav.definir_modo_hold()

        t = time.monotonic()
        await asyncio.create_task(dentro())
        assert c.primeiro_stop_apos(t) is not None
    finally:
        await c.encerrar()


def test_origens_humanas_sao_as_do_operador_e_do_notebook():
    assert nc.ORIGENS_HUMANAS_DE_PARADA == {"operador", "notebook"}


# ---------- C5-04: parar AGE sempre; a frase so morre se for so a ordem ----------


@pytest.mark.parametrize(
    "frase",
    ["Fofao, para!", "para tudo agora", "pare de andar", "jarvis pare por favor", "STOP", "parar"],
)
def test_ordem_de_parar_pura(frase):
    assert mp.MissionPlanner._pediu_para_parar(frase)
    assert mp.MissionPlanner._so_ordem_de_parar(frase)


@pytest.mark.parametrize(
    "frase",
    ["quero parar de fumar", "vou parar no mercado depois", "pare de me chamar de senhor"],
)
def test_frase_com_conteudo_para_e_segue_a_conversa(frase):
    assert mp.MissionPlanner._pediu_para_parar(frase)
    assert not mp.MissionPlanner._so_ordem_de_parar(frase)


class _IaQueResponde:
    def __init__(self):
        self.perguntas = []

    async def responder(self, mensagens, *args, **kwargs):
        self.perguntas.append(mensagens)
        return "Boa decisao! Posso te ajudar com dicas."

    def __getattr__(self, nome):
        async def _nada(*a, **k):
            return None

        return _nada


@pytest.mark.asyncio
async def test_quero_parar_de_fumar_para_as_rodas_e_responde():
    enviados = []

    async def hw(comando, parametros=None):
        enviados.append(comando)

    ia = _IaQueResponde()
    planner = mp.MissionPlanner(ia, enviar_comando_hardware=hw)
    resposta = await planner.processar("quero parar de fumar")
    assert enviados[:1] == ["STOP"]  # AGIR: parou mesmo assim
    assert resposta != mp.RESPOSTA_PARAR  # FALAR: a frase nao morreu no atalho


# ---------- C6-01: o Pi fica sabendo da cabeca que a visao do Notebook moveu ----------


@pytest.mark.asyncio
async def test_gancho_de_repasse_ve_so_o_que_saiu_para_o_corpo():
    vistos = []

    async def gancho(m):
        vistos.append(m.payload)

    bus = EventBus()
    comm = ComunicacaoService("motion_core", bus)
    note = FakeTransporte()
    esp = FakeTransporte()
    comm.adicionar_link("mission_core", note)
    comm.adicionar_link("hardware_core", esp)
    comm.definir_gancho_repasse("hardware_core", gancho)
    comm.definir_porteiro(
        "hardware_core",
        lambda p: "trava" if p.get("comando") == "MOVE_FORWARD" else None,
    )
    cabeca = Mensagem.nova(
        TipoMensagem.COMMAND, "mission_core", "hardware_core",
        {"comando": "SET_PAN_TILT", "pan_graus": 35.0, "tilt_graus": 5.0},
    )
    await note.injetar(cabeca.to_bytes())
    await note.injetar(_comando_do_notebook("MOVE_FORWARD").to_bytes())  # recusado
    await asyncio.sleep(0.05)
    assert [v["comando"] for v in vistos] == ["SET_PAN_TILT"]
    assert vistos[0]["pan_graus"] == 35.0
    await comm.encerrar()
