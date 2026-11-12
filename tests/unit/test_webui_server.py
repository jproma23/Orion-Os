"""Testes da interface web do Raspberry (Cap 13 s.4-5, 7)."""
import asyncio
import contextlib

import pytest

aiohttp = pytest.importorskip("aiohttp")
import pytest_asyncio  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from motion_core.memory.api import MemoryAPI  # noqa: E402
from motion_core.memory.database import DatabaseManager  # noqa: E402
from motion_core.webui.server import VARIAVEL_SENHA_CONFIG, WebUIServer  # noqa: E402
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
    await bus.publish("system.ready", {"robot_name": "Jarvis", "modo": "REAL"})
    await bus.aguardar_fila_vazia()

    resposta = await http.get("/estado")
    corpo = await resposta.json()
    assert corpo["estado"]["sistema"] == {"robot_name": "Jarvis", "modo": "REAL"}


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
            "temperatura_c": 25.0,
            "umidade_percent": 60.0,
            "bussola_conectada": True,
            "rumo_graus": 85.0,
            "lidar_leituras": [{"angulo": 90, "distancia_cm": 42.0, "valida": True}],
        }
    }
    await bus.publish("comm.mensagem.telemetry", envelope)
    await bus.aguardar_fila_vazia()

    resposta = await http.get("/estado")
    corpo = await resposta.json()
    hardware = corpo["estado"]["hardware"]
    assert hardware["temperatura_c"] == 25.0
    assert hardware["umidade_percent"] == 60.0
    assert hardware["bussola_conectada"] is True
    assert hardware["rumo_graus"] == 85.0
    assert hardware["lidar_leituras"] == [
        {"angulo": 90, "distancia_cm": 42.0, "valida": True}
    ]
    assert hardware["estado"] == "IDLE"
    assert hardware["em_movimento"] is False


@pytest.mark.asyncio
async def test_safe_mode_entrada_e_saida(cenario):
    http, bus = cenario
    await bus.publish("safety.safe_mode_entered", {"motivo": "perigo_frontal"})
    await bus.aguardar_fila_vazia()
    corpo = await (await http.get("/estado")).json()
    assert corpo["estado"]["seguranca"] == {"safe_mode_ativo": True, "motivo": "perigo_frontal"}

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
async def test_encerrar_nao_trava_com_cliente_sse_conectado():
    """BUG REAL (2026-07-27, vistoria pos-gravacao de firmware no Mega):
    `_handler_sse` e um `while True` de proposito (mantem o SSE vivo
    enquanto o cliente - o dashboard no navegador - estiver conectado).
    `encerrar()` chamava so `AppRunner.cleanup()`, que espera os handlers
    em andamento TERMINAREM SOZINHOS antes de retornar - com um cliente
    de verdade conectado, esse handler nunca termina sozinho. Em producao
    isso travou o desligamento gracioso do Motion Core por ate 90s (o
    padrao do systemd), so resolvido por SIGKILL - com o dashboard aberto
    numa aba do navegador, bastava isso para travar TODO restart do
    servico.

    Reproduzido aqui com um cliente SSE de verdade ainda conectado
    (dentro do `async with`) no momento de chamar encerrar() - tem que
    retornar rapido, nao esperar o proximo ping (15s, _INTERVALO_PING_S)."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    servidor = WebUIServer(bus)

    async with TestClient(TestServer(servidor._app)) as http:
        async with http.get("/eventos") as resposta:
            assert resposta.status == 200
            assert len(servidor._clientes) == 1  # cliente de verdade conectado

            await asyncio.wait_for(servidor.encerrar(), timeout=2.0)

    bus.parar()
    tarefa_bus.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa_bus



# ---------- /comando (controle manual, Cap 13 s.4) ----------
#
# Ate 2026-07-27 este endpoint chamava comm.send("hardware_core", ...)
# DIRETO, sem teste nenhum cobrindo o caminho - foi por isso que o
# descompasso (webui aceitava MOVE_CONTINUOUS/MOVE_DISTANCE, mas
# NavigationCore.executar_manual so aceitava MOVE_FORWARD) passou
# despercebido. Agora o endpoint so publica no Event Bus (regra 1); quem
# fala com o hardware e o NavigationCore, testado a parte em
# test_navigation_core.py.


@pytest.mark.asyncio
async def test_comando_nao_permitido_e_rejeitado(cenario):
    http, bus = cenario
    comandos_nav = []
    bus.subscribe("navigation.comando", lambda e: comandos_nav.append(e.dados))

    resposta = await http.post("/comando", json={"comando": "AUTODESTRUIR"})
    assert resposta.status == 400
    corpo = await resposta.json()
    assert corpo["erro"] == "comando nao permitido"
    assert comandos_nav == []  # nada foi publicado


@pytest.mark.asyncio
async def test_comando_corpo_invalido_e_rejeitado(cenario):
    http, _ = cenario
    resposta = await http.post("/comando", data="isto nao e json")
    assert resposta.status == 400


@pytest.mark.asyncio
async def test_comando_valido_publica_navigation_comando_manual(cenario):
    http, bus = cenario
    comandos_nav = []
    bus.subscribe("navigation.comando", lambda e: comandos_nav.append(e.dados))

    resposta = await http.post("/comando", json={"comando": "stop"})  # minusculo, tolerado
    assert resposta.status == 200
    corpo = await resposta.json()
    assert corpo == {"ok": True, "comando": "STOP"}

    await bus.aguardar_fila_vazia()
    assert {"acao": "MANUAL", "comando": "STOP"} in comandos_nav


@pytest.mark.asyncio
async def test_comando_velocidade_e_limitada_pelo_piso_e_teto():
    from orion.kernel.config import ConfigurationManager

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos_nav = []
    bus.subscribe("navigation.comando", lambda e: comandos_nav.append(e.dados))
    config = ConfigurationManager("config/orion.yaml").carregar()
    servidor = WebUIServer(bus, config=config)
    teto = float(config.secao("motion")["max_speed_percent"])

    async with TestClient(TestServer(servidor._app)) as http:
        resposta = await http.post(
            "/comando", json={"comando": "MOVE_CONTINUOUS", "velocidade_percent": 999}
        )
        assert resposta.status == 200
        await bus.aguardar_fila_vazia()
        assert comandos_nav[-1]["velocidade_percent"] == teto  # nunca acima do config

    bus.parar()
    tarefa_bus.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa_bus


@pytest.mark.asyncio
async def test_comando_move_distance_limita_re_maxima(cenario):
    http, bus = cenario
    comandos_nav = []
    bus.subscribe("navigation.comando", lambda e: comandos_nav.append(e.dados))

    resposta = await http.post(
        "/comando", json={"comando": "MOVE_DISTANCE", "distancia_cm": -500}
    )
    assert resposta.status == 200
    await bus.aguardar_fila_vazia()
    assert comandos_nav[-1]["distancia_cm"] == -15.0  # RE_MAXIMA_CM


@pytest.mark.asyncio
async def test_comando_move_distance_sem_distancia_e_rejeitado(cenario):
    http, _ = cenario
    resposta = await http.post("/comando", json={"comando": "MOVE_DISTANCE"})
    assert resposta.status == 400


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


# ---------------------------------------------------------------------------
# DIRIGIR PELA REDE EXIGE SESSÃO (2026-08-04)
#
# Até a varredura de hoje `/cabeca` exigia sessão e `/comando` não: virar a
# cabeça pedia senha, avançar e dar ré não. Estes testes prendem as duas
# metades da regra - o que move exige, e o PARAR nunca exige.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_comando_que_move_exige_sessao_de_fora(cenario, monkeypatch):
    http, _ = cenario
    # o TestClient conecta pelo loopback, que é liberado por projeto; fingir que
    # a requisição veio da rede é o que põe o caminho protegido à prova
    monkeypatch.setattr(WebUIServer, "_acesso_local_permitido", staticmethod(lambda r: False))
    monkeypatch.setattr(WebUIServer, "_senha_exigida", lambda self: True)

    resposta = await http.post("/comando", json={"comando": "MOVE_FORWARD"})

    assert resposta.status == 401
    corpo = await resposta.json()
    assert corpo["precisa_senha"] is True


@pytest.mark.asyncio
async def test_parar_nunca_exige_senha(cenario, monkeypatch):
    """O STOP é o item mais importante desta interface.

    Quem vê o robô indo para um lugar ruim precisa poder pará-lo AGORA - exigir
    senha para PARAR trocaria um risco por outro pior. Mesma razão pela qual o
    STOP já estava fora da disputa do volante.
    """
    http, bus = cenario
    monkeypatch.setattr(WebUIServer, "_acesso_local_permitido", staticmethod(lambda r: False))
    monkeypatch.setattr(WebUIServer, "_senha_exigida", lambda self: True)
    recebidos = []
    bus.subscribe("navigation.comando", lambda e: recebidos.append(e.dados))

    resposta = await http.post("/comando", json={"comando": "STOP"})
    await bus.aguardar_fila_vazia()

    assert resposta.status == 200
    assert recebidos and recebidos[0]["comando"] == "STOP"


@pytest.mark.asyncio
async def test_luz_e_ventilador_tambem_passam_sem_senha(cenario, monkeypatch):
    # não movem nada - entram pela mesma porta do STOP
    http, _ = cenario
    monkeypatch.setattr(WebUIServer, "_acesso_local_permitido", staticmethod(lambda r: False))
    monkeypatch.setattr(WebUIServer, "_senha_exigida", lambda self: True)

    for comando in ("LIGHT_ON", "FAN_OFF"):
        resposta = await http.post("/comando", json={"comando": comando})
        assert resposta.status == 200, comando


@pytest.mark.asyncio
async def test_trocar_o_volante_exige_sessao_de_fora(cenario, monkeypatch):
    http, _ = cenario
    monkeypatch.setattr(WebUIServer, "_acesso_local_permitido", staticmethod(lambda r: False))
    monkeypatch.setattr(WebUIServer, "_senha_exigida", lambda self: True)

    resposta = await http.post("/controle", json={"modo": "manual"})

    assert resposta.status == 401
    corpo = await resposta.json()
    assert corpo["precisa_senha"] is True


@pytest.mark.asyncio
async def test_depois_de_entrar_com_a_senha_o_robo_obedece(cenario, monkeypatch):
    """O caminho completo: 401, entra, e o mesmo comando passa.

    Sem este, os testes acima provariam só que a porta fecha - não que ela abre.
    """
    http, bus = cenario
    monkeypatch.setattr(WebUIServer, "_acesso_local_permitido", staticmethod(lambda r: False))
    monkeypatch.setattr(WebUIServer, "_senha_exigida", lambda self: True)
    monkeypatch.setenv(VARIAVEL_SENHA_CONFIG, "abre-te-sesamo")
    recebidos = []
    bus.subscribe("navigation.comando", lambda e: recebidos.append(e.dados))

    assert (await http.post("/comando", json={"comando": "MOVE_FORWARD"})).status == 401

    entrada = await http.post("/api/configuracao/entrar", json={"senha": "abre-te-sesamo"})
    assert entrada.status == 200  # o cookie de sessão fica guardado no TestClient

    resposta = await http.post("/comando", json={"comando": "MOVE_FORWARD"})
    await bus.aguardar_fila_vazia()

    assert resposta.status == 200
    assert recebidos and recebidos[0]["comando"] == "MOVE_FORWARD"


@pytest.mark.asyncio
async def test_senha_errada_nao_abre_a_porta(cenario, monkeypatch):
    http, _ = cenario
    monkeypatch.setattr(WebUIServer, "_acesso_local_permitido", staticmethod(lambda r: False))
    monkeypatch.setattr(WebUIServer, "_senha_exigida", lambda self: True)
    monkeypatch.setenv(VARIAVEL_SENHA_CONFIG, "abre-te-sesamo")

    entrada = await http.post("/api/configuracao/entrar", json={"senha": "chute"})

    assert entrada.status == 401
    assert (await http.post("/comando", json={"comando": "MOVE_FORWARD"})).status == 401


@pytest.mark.asyncio
async def test_teto_de_sessoes_descarta_a_mais_antiga_e_nao_a_nova(monkeypatch):
    """`set.pop()` removia um elemento ARBITRÁRIO - podia ser justamente o token
    recém-criado, e a pessoa acertava a senha para já entrar deslogada."""
    from motion_core.webui.server import MAXIMO_SESSOES

    bus = EventBus()
    servidor = WebUIServer(bus)
    monkeypatch.setenv(VARIAVEL_SENHA_CONFIG, "abre-te-sesamo")
    for i in range(MAXIMO_SESSOES):
        servidor._sessoes[f"antigo-{i}"] = float(i)

    async with TestClient(TestServer(servidor._app)) as http:
        resposta = await http.post(
            "/api/configuracao/entrar", json={"senha": "abre-te-sesamo"}
        )

    assert resposta.status == 200
    assert len(servidor._sessoes) == MAXIMO_SESSOES
    assert "antigo-0" not in servidor._sessoes  # saiu o mais antigo
    # e o novo continua valendo: era ele que o set.pop() podia levar
    assert any(nome.startswith("antigo-") is False for nome in servidor._sessoes)


@pytest.mark.asyncio
async def test_sem_senha_por_padrao_dirige_de_fora(cenario, monkeypatch):
    """Decisao do dono (2026-11-01): sem senha. De fora da rede, mover passa."""
    http, _ = cenario
    monkeypatch.setattr(WebUIServer, "_acesso_local_permitido", staticmethod(lambda r: False))

    resposta = await http.post("/comando", json={"comando": "MOVE_FORWARD"})

    assert resposta.status != 401


@pytest.mark.asyncio
async def test_firmware_de_outra_versao_aparece_no_painel(cenario):
    """A6: `comm.protocol_mismatch` era publicado no vazio - o painel so
    mostrava "Hardware Core nao encontrado", e parecia cabo solto."""
    http, bus = cenario
    await bus.publish(
        "comm.protocol_mismatch",
        {"destino": "hardware_core", "versao_recebida": "1.0", "versao_esperada": "2.0"},
    )
    await bus.aguardar_fila_vazia()
    corpo = await (await http.get("/estado")).json()
    erros = corpo["estado"]["diagnostico"]["ultimos_erros"]
    assert erros and erros[-1]["topico"] == "comm.protocol_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("topico", ["database.rebuilt", "database.backup_failed"])
async def test_banco_recriado_ou_backup_falho_aparece_nos_erros(cenario, topico):
    """Varredura de 12/11: os dois eram publicados no vazio - banco recriado do
    zero (memoria perdida) era so uma linha de log."""
    http, bus = cenario
    await bus.publish(topico, {"caminho": "/mnt/ssd/orion/orion.db"})
    await bus.aguardar_fila_vazia()
    corpo = await (await http.get("/estado")).json()
    assert corpo["estado"]["diagnostico"]["ultimos_erros"][-1]["topico"] == topico


@pytest.mark.asyncio
async def test_nav2_subindo_chega_ao_painel(cenario):
    http, bus = cenario
    await bus.publish("navigation.nav2_subindo", {"espera_estimada_s": 15})
    await bus.aguardar_fila_vazia()
    # /estado e nao /eventos: este e o fluxo SSE, que nao termina
    corpo = await (await http.get("/estado")).json()
    assert any(e["topico"] == "navigation.nav2_subindo" for e in corpo["eventos_recentes"])
