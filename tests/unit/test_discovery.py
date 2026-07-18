"""Testes de descoberta de dispositivos (Cap 14 s.8)."""
import asyncio

import pytest

from orion.communication.discovery import ErroVersaoIncompativel, descobrir
from orion.communication.protocol import VERSAO_PROTOCOLO
from orion.kernel.event_bus import EventBus

from conftest import FakeTransporte


@pytest.mark.asyncio
async def test_descobrir_com_resposta_automatica_da_outra_ponta():
    """Como o ComunicacaoService ja responde WHO_ARE_YOU sozinho, conectamos
    dois servicos de verdade um no outro via FakeTransporte."""
    from orion.communication.service import ComunicacaoService

    bus_a = EventBus()
    bus_b = EventBus()
    tarefa_a = asyncio.create_task(bus_a.iniciar())
    tarefa_b = asyncio.create_task(bus_b.iniciar())

    servico_a = ComunicacaoService("mission_core", bus_a, versao_modulo="1.2.3")
    servico_b = ComunicacaoService("motion_core", bus_b, versao_modulo="4.5.6")

    canal_a_para_b: asyncio.Queue = asyncio.Queue()
    canal_b_para_a: asyncio.Queue = asyncio.Queue()

    transporte_a = FakeTransporte()
    transporte_a._entrada = canal_b_para_a
    transporte_a.enviar = canal_a_para_b.put

    transporte_b = FakeTransporte()
    transporte_b._entrada = canal_a_para_b
    transporte_b.enviar = canal_b_para_a.put

    servico_a.adicionar_link("motion_core", transporte_a)
    servico_b.adicionar_link("mission_core", transporte_b)

    info = await descobrir(servico_a, "motion_core", bus_a, timeout_s=2)

    assert info.nome == "motion_core"
    assert info.versao_modulo == "4.5.6"
    assert info.versao_protocolo == VERSAO_PROTOCOLO

    await servico_a.encerrar()
    await servico_b.encerrar()
    bus_a.parar()
    bus_b.parar()
    await tarefa_a
    await tarefa_b


@pytest.mark.asyncio
async def test_descobrir_versao_incompativel_publica_evento_e_falha():
    from orion.communication.service import ComunicacaoService

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    mismatches = []
    bus.subscribe("comm.protocol_mismatch", lambda e: mismatches.append(e.dados))

    servico = ComunicacaoService("mission_core", bus)
    transporte = FakeTransporte()
    servico.adicionar_link("motion_core", transporte)

    async def responder_com_versao_errada():
        while not transporte.enviados:
            await asyncio.sleep(0.01)
        from orion.communication.protocol import Mensagem, TipoMensagem

        pedido = Mensagem.from_bytes(transporte.enviados[0])
        resposta = Mensagem.nova(
            TipoMensagem.RESPONSE,
            "motion_core",
            "mission_core",
            {"nome": "motion_core", "versao_modulo": "0.1.0", "versao_protocolo": "0.9-antiga"},
            id_referencia=pedido.id,
        )
        await transporte.injetar(resposta.to_bytes())

    asyncio.create_task(responder_com_versao_errada())

    with pytest.raises(ErroVersaoIncompativel):
        await descobrir(servico, "motion_core", bus, timeout_s=2)

    await bus.aguardar_fila_vazia()
    assert len(mismatches) == 1
    assert mismatches[0]["versao_recebida"] == "0.9-antiga"

    await servico.encerrar()
    bus.parar()
    await tarefa_bus
