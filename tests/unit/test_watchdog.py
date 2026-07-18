"""Testes do Health Monitor + Watchdog (Cap 6 secao 8)."""
import pytest

from orion.kernel.event_bus import EventBus
from orion.kernel.watchdog import HealthMonitor, Watchdog


def test_modulo_recente_nao_e_considerado_perdido():
    hm = HealthMonitor(intervalo_heartbeat_s=1.0, heartbeats_perdidos_limite=3)
    hm.registrar_modulo("vision", agora=100.0)

    assert hm.modulos_com_heartbeat_perdido(agora=100.5) == []


def test_modulo_sem_heartbeat_alem_do_timeout_e_perdido():
    hm = HealthMonitor(intervalo_heartbeat_s=1.0, heartbeats_perdidos_limite=3)
    hm.registrar_modulo("vision", agora=100.0)

    # timeout = 1.0 * 3 = 3s
    assert hm.modulos_com_heartbeat_perdido(agora=104.0) == ["vision"]


def test_receber_heartbeat_reseta_o_relogio():
    hm = HealthMonitor(intervalo_heartbeat_s=1.0, heartbeats_perdidos_limite=3)
    hm.registrar_modulo("vision", agora=100.0)
    hm.receber_heartbeat("vision", agora=103.9)

    assert hm.modulos_com_heartbeat_perdido(agora=104.0) == []


def test_receber_heartbeat_de_modulo_nao_registrado_falha():
    hm = HealthMonitor(intervalo_heartbeat_s=1.0, heartbeats_perdidos_limite=3)
    with pytest.raises(KeyError):
        hm.receber_heartbeat("fantasma")


@pytest.mark.asyncio
async def test_watchdog_tenta_reconectar_antes_de_reiniciar():
    hm = HealthMonitor(intervalo_heartbeat_s=1.0, heartbeats_perdidos_limite=1)
    chamadas = []

    def reconectar():
        chamadas.append("reconectar")
        return True  # sucesso: nao deve chamar reiniciar

    def reiniciar():
        chamadas.append("reiniciar")

    hm.registrar_modulo("vision", reconectar=reconectar, reiniciar=reiniciar, agora=0.0)

    watchdog = Watchdog(hm)
    perdidos = await watchdog.verificar_uma_vez(agora=100.0)

    assert perdidos == ["vision"]
    assert chamadas == ["reconectar"]


@pytest.mark.asyncio
async def test_watchdog_reinicia_quando_reconexao_falha():
    hm = HealthMonitor(intervalo_heartbeat_s=1.0, heartbeats_perdidos_limite=1)
    chamadas = []

    def reconectar():
        chamadas.append("reconectar")
        return False  # falha explicita

    def reiniciar():
        chamadas.append("reiniciar")

    hm.registrar_modulo("vision", reconectar=reconectar, reiniciar=reiniciar, agora=0.0)

    watchdog = Watchdog(hm)
    await watchdog.verificar_uma_vez(agora=100.0)

    assert chamadas == ["reconectar", "reiniciar"]


@pytest.mark.asyncio
async def test_watchdog_publica_diagnostic_error():
    hm = HealthMonitor(intervalo_heartbeat_s=1.0, heartbeats_perdidos_limite=1)
    hm.registrar_modulo("vision", agora=0.0)

    bus = EventBus()
    recebidos = []
    bus.subscribe("diagnostic.error", lambda evento: recebidos.append(evento.dados))

    import asyncio

    tarefa = asyncio.create_task(bus.iniciar())
    watchdog = Watchdog(hm, event_bus=bus)
    await watchdog.verificar_uma_vez(agora=100.0)
    await bus.aguardar_fila_vazia()
    bus.parar()
    await tarefa

    assert recebidos == [{"modulo": "vision", "motivo": "heartbeat_perdido", "tentativa": 1}]
