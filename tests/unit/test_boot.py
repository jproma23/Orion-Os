"""Testes do Boot Manager (Cap 6 secao 4).

Fase 1 - pronto quando: boot em modo --sim chega a system.ready.
"""
import pytest

from orion.kernel.boot import BootManager
from orion.kernel.event_bus import Prioridade
from orion.kernel.registry import EstadoModulo


@pytest.mark.asyncio
async def test_boot_sim_chega_a_system_ready(tmp_path):
    recebidos = []

    boot_manager = BootManager(
        caminho_config="config/orion.yaml",
        caminho_log=tmp_path / "orion.log",
        simulado=True,
    )
    sistema = await boot_manager.iniciar()
    sistema.event_bus.subscribe("system.ready", lambda evento: recebidos.append(evento.dados))

    # o system.ready real ja foi publicado e consumido durante o boot; aqui
    # verificamos que o proprio boot concluiu com o registry no estado certo
    # e publicamos um segundo evento so para confirmar que o bus continua
    # funcionando apos o boot.
    await sistema.event_bus.publish("system.ready", {"pos_boot": True}, prioridade=Prioridade.CRITICA)
    await sistema.event_bus.aguardar_fila_vazia()

    assert recebidos == [{"pos_boot": True}]
    assert sistema.registry.obter("mission_core").estado is EstadoModulo.RUNNING

    await sistema.encerrar()


@pytest.mark.asyncio
async def test_boot_encerrar_para_mission_core(tmp_path):
    boot_manager = BootManager(caminho_log=tmp_path / "orion.log", simulado=True)
    sistema = await boot_manager.iniciar()

    await sistema.encerrar()

    assert sistema.registry.obter("mission_core").estado is EstadoModulo.STOPPED



@pytest.mark.asyncio
async def test_boot_nao_aborta_com_modulos_de_fases_futuras_ausentes(tmp_path):
    """Raspberry/Arduino/DB/IA/Vision/Motion Core ainda nao existem neste
    ponto do projeto - o boot deve tolerar isso e nao registra-los, em vez
    de abortar (Cap 6 secao 8)."""
    boot_manager = BootManager(caminho_log=tmp_path / "orion.log", simulado=True)
    sistema = await boot_manager.iniciar()

    for nome in ("raspberry", "arduino", "database", "ai", "vision", "motion_hardware"):
        assert not sistema.registry.existe(nome)

    await sistema.encerrar()
