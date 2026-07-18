"""Testes da interface web do Raspberry (Cap 13 s.4-5, 7)."""
import asyncio
import contextlib

import pytest

aiohttp = pytest.importorskip("aiohttp")
import pytest_asyncio  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from motion_core.memory.api import MemoryAPI  # noqa: E402
from motion_core.memory.database import DatabaseManager  # noqa: E402
from motion_core.webui.server import WebUIServer  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402


@pytest_asyncio.fixture
async def cenario():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    servidor = WebUIServer(bus)
    async with TestClient(TestServer(servidor._app)) as cliente:
        yield cliente, bus
    bus.parar()
    tarefa_bus.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa_bus


@pytest.mark.asyncio
async def test_index_serve_o_html_do_dashboard(cenario):
    http, _ = cenario
    resposta = await http.get("/")
    assert resposta.status == 200
    assert "text/html" in resposta.headers["Content-Type"]


@pytest.mark.asyncio
async def test_diagnostico_serve_o_html(cenario):
    http, _ = cenario
    resposta = await http.get("/diagnostico")
    assert resposta.status == 200
    assert "text/html" in resposta.headers["Content-Type"]


@pytest.mark.asyncio
async def test_log_retorna_estrutura_valida(cenario):
    http, _ = cenario
    resposta = await http.get("/log")
    assert resposta.status == 200
    corpo = await resposta.json()
    assert isinstance(corpo["linhas"], list)


@pytest.mark.asyncio
async def test_diagnostic_error_registra_ultimo_erro(cenario):
    http, bus = cenario
    await bus.publish("diagnostic.error", {"modulo": "vision", "motivo": "camera_nao_encontrada"})
    await bus.aguardar_fila_vazia()

    corpo = await (await http.get("/estado")).json()
    erros = corpo["estado"]["diagnostico"]["ultimos_erros"]
    assert len(erros) == 1
    assert erros[0]["dados"] == {"modulo": "vision", "motivo": "camera_nao_encontrada"}


@pytest.mark.asyncio
async def test_module_lost_e_recovered_atualizam_saude(cenario):
    http, bus = cenario
    await bus.publish("comm.module_lost", {"modulo": "hardware_core"})
    await bus.aguardar_fila_vazia()
    corpo = await (await http.get("/estado")).json()
    assert corpo["estado"]["diagnostico"]["modulos"]["hardware_core"]["status"] == "perdido"

    await bus.publish("comm.module_recovered", {"modulo": "hardware_core"})
    await bus.aguardar_fila_vazia()
    corpo = await (await http.get("/estado")).json()
    assert corpo["estado"]["diagnostico"]["modulos"]["hardware_core"]["status"] == "ok"


@pytest.mark.asyncio
async def test_mapa_serve_o_html_do_radar(cenario):
    http, _ = cenario
    resposta = await http.get("/mapa")
    assert resposta.status == 200
    assert "text/html" in resposta.headers["Content-Type"]


@pytest.mark.asyncio
async def test_scan_complete_atualiza_o_mapa(cenario):
    http, bus = cenario
    leituras = [
        {"angulo": 0, "distancia_cm": 30, "valida": True},
        {"angulo": 90, "distancia_cm": -1, "valida": False},
    ]
    await bus.publish("motion.scan_complete", {"leituras": leituras})
    await bus.aguardar_fila_vazia()

    corpo = await (await http.get("/estado")).json()
    assert corpo["estado"]["mapa"] == {"leituras": leituras}


@pytest.mark.asyncio
async def test_estado_inicial_e_vazio_mas_bem_formado(cenario):
    http, _ = cenario
    resposta = await http.get("/estado")
    assert resposta.status == 200
    corpo = await resposta.json()
    assert corpo["estado"]["sistema"]["robot_name"] is None
    assert corpo["estado"]["seguranca"]["safe_mode_ativo"] is False
    assert corpo["estado"]["mapa"] == {"leituras": []}
    assert corpo["estado"]["diagnostico"] == {"ultimos_erros": [], "modulos": {}}
    assert corpo["eventos_recentes"] == []


@pytest.mark.asyncio
async def test_system_ready_atualiza_estado(cenario):
    http, bus = cenario
    await bus.publish("system.ready", {"robot_name": "Fofão", "modo": "REAL"})
    await bus.aguardar_fila_vazia()

    resposta = await http.get("/estado")
    corpo = await resposta.json()
    assert corpo["estado"]["sistema"] == {"robot_name": "Fofão", "modo": "REAL"}


@pytest.mark.asyncio
async def test_motion_position_atualiza_estado(cenario):
    http, bus = cenario
    dados = {"x_m": 1.2, "y_m": 0.5, "orientacao_graus": 90.0, "velocidade_m_s": 0.3}
    await bus.publish("motion.position", dados)
    await bus.aguardar_fila_vazia()

    resposta = await http.get("/estado")
    corpo = await resposta.json()
    assert corpo["estado"]["posicao"] == dados


@pytest.mark.asyncio
async def test_telemetria_atualiza_campos_de_hardware(cenario):
    http, bus = cenario
    # TELEMETRY chega com o envelope completo (Mensagem.to_dict()), nao so
    # o payload interno - mesmo formato real de comm.mensagem.telemetry.
    envelope = {
        "payload": {
            "estado": "IDLE",
            "em_movimento": False,
            "distancia_frontal_cm": 42.0,
            "temperatura_c": 25.0,
            "umidade_percent": 60.0,
            "inclinacao_graus": 2.5,
        }
    }
    await bus.publish("comm.mensagem.telemetry", envelope)
    await bus.aguardar_fila_vazia()

    resposta = await http.get("/estado")
    corpo = await resposta.json()
    hardware = corpo["estado"]["hardware"]
    assert hardware["distancia_frontal_cm"] == 42.0
    assert hardware["temperatura_c"] == 25.0
    assert hardware["umidade_percent"] == 60.0
    assert hardware["inclinacao_graus"] == 2.5
    assert hardware["estado"] == "IDLE"
    assert hardware["em_movimento"] is False


@pytest.mark.asyncio
async def test_safe_mode_entrada_e_saida(cenario):
    http, bus = cenario
    await bus.publish("safety.safe_mode_entered", {"motivo": "impacto_detectado"})
    await bus.aguardar_fila_vazia()
    corpo = await (await http.get("/estado")).json()
    assert corpo["estado"]["seguranca"] == {"safe_mode_ativo": True, "motivo": "impacto_detectado"}

    await bus.publish("safety.safe_mode_exited", {})
    await bus.aguardar_fila_vazia()
    corpo = await (await http.get("/estado")).json()
    assert corpo["estado"]["seguranca"] == {"safe_mode_ativo": False, "motivo": None}


@pytest.mark.asyncio
async def test_eventos_recentes_registra_e_limita_tamanho(cenario):
    http, bus = cenario
    for i in range(35):
        await bus.publish("navigation.mode_changed", {"modo": f"HOLD{i}"})
    await bus.aguardar_fila_vazia()

    corpo = await (await http.get("/estado")).json()
    assert len(corpo["eventos_recentes"]) == 30
    # os 30 mais recentes: o mais antigo mantido e o indice 5 (0..4 descartados)
    assert corpo["eventos_recentes"][0]["dados"]["modo"] == "HOLD5"
    assert corpo["eventos_recentes"][-1]["dados"]["modo"] == "HOLD34"


@pytest.mark.asyncio
async def test_evento_e_repassado_para_cliente_sse(cenario):
    http, bus = cenario
    async with http.get("/eventos") as resposta:
        assert resposta.status == 200
        assert resposta.headers["Content-Type"] == "text/event-stream"

        await bus.publish("navigation.mode_changed", {"modo": "PATROL"})

        linha = await resposta.content.readline()
        assert linha == b"event: navigation.mode_changed\n"
        linha_dados = await resposta.content.readline()
        assert linha_dados == b'data: {"modo": "PATROL"}\n'


@pytest.mark.asyncio
async def test_topico_nao_repassado_e_ignorado():
    bus = EventBus()
    WebUIServer(bus)
    assert "motion.scan_complete" in bus._assinantes  # esse esta na lista
    assert "comm.mensagem.command" not in bus._assinantes  # topico real (Cap 14), mas fora da lista


@pytest.mark.asyncio
async def test_conversa_serve_o_html(cenario):
    http, _ = cenario
    resposta = await http.get("/conversa")
    assert resposta.status == 200
    assert "text/html" in resposta.headers["Content-Type"]


@pytest.mark.asyncio
async def test_api_conversas_sem_memory_api_retorna_aviso(cenario):
    http, _ = cenario
    resposta = await http.get("/api/conversas")
    corpo = await resposta.json()
    assert corpo == {"conversas": [], "aviso": "banco de dados nao disponivel"}


@pytest.mark.asyncio
async def test_api_conversas_com_memory_api_retorna_historico(tmp_path):
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    db = DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")
    db.iniciar()
    memory = MemoryAPI(db, bus)
    await memory.remember("conversas", {"pessoa_id": None, "papel": "usuario", "texto": "oi"})
    await memory.remember("conversas", {"pessoa_id": None, "papel": "robo", "texto": "olá!"})

    servidor = WebUIServer(bus, memory_api=memory)
    async with TestClient(TestServer(servidor._app)) as http:
        resposta = await http.get("/api/conversas")
        corpo = await resposta.json()

    bus.parar()
    tarefa_bus.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa_bus

    assert corpo["aviso"] is None
    assert [c["texto"] for c in corpo["conversas"]] == ["oi", "olá!"]  # ordem cronologica


def test_acesso_local_permitido_aceita_loopback():
    from types import SimpleNamespace

    assert WebUIServer._acesso_local_permitido(SimpleNamespace(remote="127.0.0.1")) is True
    assert WebUIServer._acesso_local_permitido(SimpleNamespace(remote="::1")) is True


def test_acesso_local_permitido_rejeita_remoto():
    from types import SimpleNamespace

    assert WebUIServer._acesso_local_permitido(SimpleNamespace(remote="10.20.20.195")) is False


@pytest.mark.asyncio
async def test_configuracao_via_testclient_e_permitido(cenario):
    # o TestClient do aiohttp conecta via loopback de verdade - serve pra
    # provar que o caminho "acesso local permitido" funciona ponta a ponta
    http, _ = cenario
    resposta = await http.get("/configuracao")
    assert resposta.status == 200


@pytest.mark.asyncio
async def test_api_configuracao_sem_config_retorna_aviso(cenario):
    http, _ = cenario
    resposta = await http.get("/api/configuracao")
    corpo = await resposta.json()
    assert corpo == {"parametros": {}, "aviso": "configuracao nao disponivel"}


@pytest.mark.asyncio
async def test_api_configuracao_com_config_retorna_parametros():
    from orion.kernel.config import ConfigurationManager

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    config = ConfigurationManager("config/orion.yaml").carregar()
    servidor = WebUIServer(bus, config=config)

    async with TestClient(TestServer(servidor._app)) as http:
        resposta = await http.get("/api/configuracao")
        corpo = await resposta.json()

    bus.parar()
    tarefa_bus.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa_bus

    assert corpo["aviso"] is None
    assert corpo["parametros"]["system"]["robot_name"]
