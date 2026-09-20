"""Testes do GerenteNav2 - o Nav2 ligado so quando o robo precisa navegar.

Medido no Pi (2026-09-20): o container custa 225 MB dos 3,8 GB e leva 15 s
para o bridge responder. Estes testes nao chamam docker nenhum - trocam
`_docker` e a sonda HTTP por falsos e provam a LOGICA do ciclo de vida.
"""
import asyncio
import logging

import pytest

from motion_core.navigation import gerente_nav2 as modulo
from motion_core.navigation.gerente_nav2 import GerenteNav2
from motion_core.navigation.nav2_goal import Nav2Goal
from orion.kernel.event_bus import EventBus


class BusFalso:
    def __init__(self):
        self.publicados = []

    async def publish(self, topico, dados=None, prioridade=None):
        self.publicados.append((topico, dados))

    def subscribe(self, topico, handler):
        pass


def _preparar(monkeypatch, gerente, *, rodando, pronto_apos):
    """Troca docker e sonda por falsos.

    `pronto_apos`: quantas sondas ainda vao falhar antes de o bridge
    responder - e o que simula os 15 s de subida do ROS.
    """
    comandos = []
    estado = {"rodando": rodando, "sondas": 0}

    async def docker_falso(*args, timeout_s=0):
        comandos.append(args)
        if args[0] == "start":
            estado["rodando"] = True
        elif args[0] == "stop":
            estado["rodando"] = False
        elif args[0] == "inspect":
            return 0, "true" if estado["rodando"] else "false"
        return 0, ""

    def sondar_falso(url):
        if not estado["rodando"]:
            return False
        estado["sondas"] += 1
        return estado["sondas"] > pronto_apos

    monkeypatch.setattr(gerente, "_docker", docker_falso)
    monkeypatch.setattr(modulo, "_sondar", sondar_falso)
    monkeypatch.setattr(modulo, "INTERVALO_SONDA_S", 0.001)
    return comandos, estado


@pytest.mark.asyncio
async def test_nav2_ja_no_ar_nao_chama_docker(monkeypatch):
    """O caso comum durante uma ronda: nao pode pagar docker a cada goal."""
    g = GerenteNav2(BusFalso())
    comandos, _ = _preparar(monkeypatch, g, rodando=True, pronto_apos=0)

    assert await g.garantir_no_ar() is True
    assert comandos == [], "com o Nav2 de pe nao ha nada a fazer"


@pytest.mark.asyncio
async def test_nav2_parado_sobe_e_espera_o_bridge(monkeypatch):
    """Container rodando != Nav2 pronto: o docker devolve na hora e o ROS
    ainda leva segundos. Quem responde e a sonda."""
    bus = BusFalso()
    g = GerenteNav2(bus)
    comandos, _ = _preparar(monkeypatch, g, rodando=False, pronto_apos=5)

    assert await g.garantir_no_ar() is True
    assert ("start", modulo.NOME_CONTAINER) in comandos
    topicos = [t for t, _ in bus.publicados]
    # avisa que esta subindo - e o que deixa o robo dizer "ja vou"
    assert "navigation.nav2_subindo" in topicos
    assert "navigation.nav2_pronto" in topicos


@pytest.mark.asyncio
async def test_dois_goals_juntos_dao_um_start_so(monkeypatch):
    """Sem a guarda, dois goals chegando juntos pedem `docker start` em
    duplicata e o segundo falha com 'already started' - o goal seria
    descartado por engano."""
    g = GerenteNav2(BusFalso())
    comandos, _ = _preparar(monkeypatch, g, rodando=False, pronto_apos=3)

    r1, r2 = await asyncio.gather(g.garantir_no_ar(), g.garantir_no_ar())

    assert (r1, r2) == (True, True)
    assert [c for c in comandos if c[0] == "start"] == [("start", modulo.NOME_CONTAINER)]


@pytest.mark.asyncio
async def test_bridge_que_nunca_responde_devolve_falso(monkeypatch):
    """Melhor dizer 'nao da para navegar' do que mandar um goal para o vazio."""
    g = GerenteNav2(BusFalso())
    _preparar(monkeypatch, g, rodando=False, pronto_apos=10_000)
    monkeypatch.setattr(modulo, "TIMEOUT_SUBIDA_S", 0.02)

    assert await g.garantir_no_ar() is False


@pytest.mark.asyncio
async def test_ocioso_desliga_e_devolve_a_ram(monkeypatch):
    bus = BusFalso()
    g = GerenteNav2(bus, ocioso_para_desligar_s=0.05)
    comandos, estado = _preparar(monkeypatch, g, rodando=True, pronto_apos=0)

    await g.garantir_no_ar()
    tarefa = asyncio.create_task(g.iniciar())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if not estado["rodando"]:
            break
    tarefa.cancel()

    assert estado["rodando"] is False, "devia ter desligado depois de ocioso"
    assert any(c[0] == "stop" for c in comandos)
    assert "navigation.nav2_desligado" in [t for t, _ in bus.publicados]


@pytest.mark.asyncio
async def test_uso_recente_nao_desliga(monkeypatch):
    """O relogio zera a cada goal - senao uma ronda com pausas curtas pagaria
    os 15 s de subida a cada perna."""
    g = GerenteNav2(BusFalso(), ocioso_para_desligar_s=10.0)
    comandos, estado = _preparar(monkeypatch, g, rodando=True, pronto_apos=0)

    await g.garantir_no_ar()
    tarefa = asyncio.create_task(g.iniciar())
    await asyncio.sleep(0.05)
    tarefa.cancel()

    assert estado["rodando"] is True
    assert not any(c[0] == "stop" for c in comandos)


@pytest.mark.asyncio
async def test_desabilitado_nao_toca_no_container(monkeypatch):
    """`nav2.sob_demanda: false` volta o modo antigo, com o Nav2 sempre de pe."""
    g = GerenteNav2(BusFalso(), habilitado=False)
    comandos, _ = _preparar(monkeypatch, g, rodando=False, pronto_apos=0)

    assert await g.garantir_no_ar() is True
    assert comandos == []
    await g.iniciar()  # volta na hora, sem laco


@pytest.mark.asyncio
async def test_goal_nao_vai_para_o_vazio_se_o_nav2_nao_sobe():
    """O elo com o Nav2Goal: sem Nav2, o goal vira falha explicita em vez de
    um POST para uma porta fechada."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    resultados = []
    bus.subscribe("navigation.goal_resultado", lambda e: resultados.append(e.dados))

    class GerenteQueFalha:
        async def garantir_no_ar(self):
            return False

        def marcar_uso(self):
            pass

    Nav2Goal(bus, gerente=GerenteQueFalha())
    await bus.publish("navigation.goal", {"x_m": 1.0, "y_m": 2.0})
    await bus.aguardar_fila_vazia()
    await asyncio.sleep(0.05)
    await bus.aguardar_fila_vazia()

    assert resultados, "quem pediu tem que saber que nao deu"
    assert resultados[0]["resultado"] == "falhou"
    assert resultados[0]["motivo"] == "nav2_indisponivel"

    bus.parar()
    tarefa_bus.cancel()


@pytest.mark.asyncio
async def test_sem_gerente_o_comportamento_antigo_continua(monkeypatch):
    """Nav2Goal(bus) sem gerente nao pode nem olhar para o docker - e assim
    que o resto da suite e o modo `sob_demanda: false` o usam."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    enviados = []

    ponte = Nav2Goal(bus)
    monkeypatch.setattr(ponte, "_enviar", lambda p: (enviados.append(p), ("completado", 200))[1])

    await bus.publish("navigation.goal", {"x_m": 1.0, "y_m": 2.0})
    await bus.aguardar_fila_vazia()
    await asyncio.sleep(0.05)

    assert enviados == [{"x": 1.0, "y": 2.0, "yaw_rad": 0.0}]

    bus.parar()
    tarefa_bus.cancel()


def test_sonda_aceita_404_como_vivo():
    """O bridge so tem /goal e /cancelar; a raiz devolve 404. Tratar 404 como
    'fora' faria o gerente esperar os 45 s inteiros e desistir de um Nav2 que
    ja estava pronto."""
    import urllib.error

    def urlopen_404(url, timeout=0):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    original = modulo.urllib.request.urlopen
    modulo.urllib.request.urlopen = urlopen_404
    try:
        assert modulo._sondar("http://127.0.0.1:8081/") is True
    finally:
        modulo.urllib.request.urlopen = original


def test_sonda_com_porta_fechada_e_falso():
    def urlopen_recusado(url, timeout=0):
        raise OSError("Connection refused")

    original = modulo.urllib.request.urlopen
    modulo.urllib.request.urlopen = urlopen_recusado
    try:
        assert modulo._sondar("http://127.0.0.1:8081/") is False
    finally:
        modulo.urllib.request.urlopen = original


logging.getLogger("motion_core.navigation.gerente_nav2").setLevel(logging.CRITICAL)
