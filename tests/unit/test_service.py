"""Testes da camada de servico de comunicacao (Cap 14 s.5-7).

Usa um FakeTransporte em memoria (sem socket/serial real) para testar a
logica de ACK/retransmissao/roteamento isoladamente - os transportes reais
ja tem seus proprios testes em test_transport.py.
"""
import asyncio

import pytest

from orion.communication.protocol import Mensagem, TipoMensagem
from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.kernel.event_bus import EventBus

from conftest import FakeTransporte


async def _rodar_bus(bus: EventBus) -> asyncio.Task:
    return asyncio.create_task(bus.iniciar())


@pytest.mark.asyncio
async def test_send_recebe_ack_e_retorna():
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    servico = ComunicacaoService("mission_core", bus, max_retries=3, ack_timeout_ms=200)
    transporte = FakeTransporte()
    servico.adicionar_link("motion_core", transporte)

    async def responder_com_ack():
        while not transporte.enviados:
            await asyncio.sleep(0.01)
        enviado = Mensagem.from_bytes(transporte.enviados[0])
        ack = Mensagem.ack(enviado, origem="motion_core")
        await transporte.injetar(ack.to_bytes())

    asyncio.create_task(responder_com_ack())
    resposta = await servico.send("motion_core", {"acao": "STOP"})

    assert resposta.tipo is TipoMensagem.ACK
    assert len(transporte.enviados) == 1  # nao devia ter retransmitido

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_send_retransmite_e_desiste_publicando_link_degraded():
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    eventos_degradados = []
    bus.subscribe("comm.link_degraded", lambda e: eventos_degradados.append(e.dados))

    servico = ComunicacaoService("mission_core", bus, max_retries=2, ack_timeout_ms=50)
    transporte = FakeTransporte()
    servico.adicionar_link("motion_core", transporte)

    with pytest.raises(ErroComunicacao):
        await servico.send("motion_core", {"acao": "STOP"})

    assert len(transporte.enviados) == 2  # tentou as 2 vezes configuradas
    await bus.aguardar_fila_vazia()
    assert len(eventos_degradados) == 1

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_sem_rota_falha_imediatamente():
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    servico = ComunicacaoService("mission_core", bus)

    with pytest.raises(ErroComunicacao):
        await servico.send("modulo_desconhecido", {})

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_mensagem_recebida_com_crc_invalido_gera_nack():
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    servico = ComunicacaoService("motion_core", bus)
    transporte = FakeTransporte()
    servico.adicionar_link("mission_core", transporte)

    original = Mensagem.nova(TipoMensagem.COMMAND, "mission_core", "motion_core", {"acao": "STOP"})
    dados = original.to_dict()
    dados["payload"] = {"acao": "algo_diferente"}  # corrompe sem atualizar o checksum
    corrompida = Mensagem.from_dict(dados)

    await transporte.injetar(corrompida.to_bytes())
    await asyncio.sleep(0.05)

    assert len(transporte.enviados) == 1
    resposta = Mensagem.from_bytes(transporte.enviados[0])
    assert resposta.tipo is TipoMensagem.NACK
    assert resposta.id_referencia == corrompida.id

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_link_com_exigir_checksum_falso_nao_nacka():
    """O link com o Arduino (firmware C++) nao reproduz a serializacao JSON
    canonica do Python - exigir_checksum_mensagem=False confia no CRC16 do
    enquadramento (ja validado ao decodificar o quadro) em vez de recalcular
    o checksum da mensagem (Cap 14 s.3)."""
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    servico = ComunicacaoService("motion_core", bus)
    transporte = FakeTransporte()
    servico.adicionar_link("hardware_core", transporte, exigir_checksum_mensagem=False)

    original = Mensagem.nova(TipoMensagem.COMMAND, "hardware_core", "motion_core", {"acao": "STOP"})
    dados = original.to_dict()
    dados["checksum"] = "0000"  # nunca bateria com o recalculo do lado Python
    mensagem = Mensagem.from_dict(dados)
    await transporte.injetar(mensagem.to_bytes())
    await asyncio.sleep(0.05)

    assert len(transporte.enviados) == 1
    resposta = Mensagem.from_bytes(transporte.enviados[0])
    assert resposta.tipo is TipoMensagem.ACK  # processou normalmente, sem NACK

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_command_recebido_gera_ack_automatico_e_evento_local():
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    eventos = []
    bus.subscribe("comm.mensagem.command", lambda e: eventos.append(e.dados))

    servico = ComunicacaoService("motion_core", bus)
    transporte = FakeTransporte()
    servico.adicionar_link("mission_core", transporte)

    comando = Mensagem.nova(TipoMensagem.COMMAND, "mission_core", "motion_core", {"acao": "STOP"})
    await transporte.injetar(comando.to_bytes())
    await asyncio.sleep(0.05)
    await bus.aguardar_fila_vazia()

    assert len(transporte.enviados) == 1
    ack = Mensagem.from_bytes(transporte.enviados[0])
    assert ack.tipo is TipoMensagem.ACK
    assert ack.id_referencia == comando.id
    assert len(eventos) == 1

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_request_aguarda_response_correlacionada():
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    servico = ComunicacaoService("mission_core", bus)
    transporte = FakeTransporte()
    servico.adicionar_link("motion_core", transporte)

    async def responder():
        while not transporte.enviados:
            await asyncio.sleep(0.01)
        pedido = Mensagem.from_bytes(transporte.enviados[0])
        resposta = Mensagem.nova(
            TipoMensagem.RESPONSE,
            "motion_core",
            "mission_core",
            {"posicao": [1, 2]},
            id_referencia=pedido.id,
        )
        await transporte.injetar(resposta.to_bytes())

    asyncio.create_task(responder())
    resposta = await servico.request("motion_core", {"consulta": "posicao"}, timeout_s=2)

    assert resposta.tipo is TipoMensagem.RESPONSE
    assert resposta.payload == {"posicao": [1, 2]}

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_publish_difunde_para_links_e_bus_local():
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    recebidos_localmente = []
    bus.subscribe("vision.person_detected", lambda e: recebidos_localmente.append(e.dados))

    servico = ComunicacaoService("mission_core", bus)
    transporte = FakeTransporte()
    servico.adicionar_link("motion_core", transporte)

    await servico.publish("vision.person_detected", {"confianca": 0.9})
    await bus.aguardar_fila_vazia()

    assert len(transporte.enviados) == 1
    evento_transmitido = Mensagem.from_bytes(transporte.enviados[0])
    assert evento_transmitido.tipo is TipoMensagem.EVENT
    assert evento_transmitido.payload["topico"] == "vision.person_detected"
    assert recebidos_localmente == [{"confianca": 0.9}]

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_roteamento_transparente_encaminha_para_outro_link():
    """Simula o Raspberry: fala com mission_core (TCP) e hardware_core
    (serial). Uma mensagem vinda do Notebook destinada ao Arduino deve ser
    encaminhada automaticamente pelo link serial (Cap 14 s.7)."""
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    servico = ComunicacaoService("motion_core", bus)
    link_notebook = FakeTransporte()
    link_arduino = FakeTransporte()
    servico.adicionar_link("mission_core", link_notebook)
    servico.adicionar_link("hardware_core", link_arduino)

    mensagem_para_arduino = Mensagem.nova(
        TipoMensagem.COMMAND, "mission_core", "hardware_core", {"acao": "MOVE_FORWARD"}
    )
    await link_notebook.injetar(mensagem_para_arduino.to_bytes())
    await asyncio.sleep(0.05)

    assert len(link_arduino.enviados) == 1
    encaminhada = Mensagem.from_bytes(link_arduino.enviados[0])
    assert encaminhada.destino == "hardware_core"
    assert encaminhada.payload == {"acao": "MOVE_FORWARD"}
    assert link_notebook.enviados == []  # nao deveria ter ACKado - nao e o destino final

    await servico.encerrar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_roteamento_reassina_checksum_de_mensagem_do_firmware():
    """Regressao (2026-07-19): uma RESPONSE do Arduino carrega o checksum
    calculado pelo firmware C++, que nao reproduz a serializacao canonica do
    Python. Ao ser encaminhada pelo Raspberry ao Notebook via TCP (enlace
    que valida checksum), chegava invalida e era NACKada - toda a cadeia
    Notebook->Arduino ficava sem resposta. O roteador deve reassinar o
    checksum ao encaminhar (a integridade da entrada ja foi garantida pelo
    CRC16 do enquadramento serial)."""
    bus = EventBus()
    tarefa_bus = await _rodar_bus(bus)
    servico = ComunicacaoService("motion_core", bus)
    link_notebook = FakeTransporte()
    link_arduino = FakeTransporte()
    servico.adicionar_link("mission_core", link_notebook)
    servico.adicionar_link("hardware_core", link_arduino, exigir_checksum_mensagem=False)

    resposta = Mensagem.nova(
        TipoMensagem.RESPONSE,
        "hardware_core",
        "mission_core",
        {"uptime_ms": 123},
        id_referencia="abc123",
    )
    resposta.checksum = "beef"  # simula o checksum nao-canonico do firmware
    await link_arduino.injetar(resposta.to_bytes())
    await asyncio.sleep(0.05)

    assert len(link_notebook.enviados) == 1
    encaminhada = Mensagem.from_bytes(link_notebook.enviados[0])
    assert encaminhada.checksum_valido()  # reassinada pelo roteador
    assert encaminhada.payload == {"uptime_ms": 123}
    assert encaminhada.id_referencia == "abc123"

    await servico.encerrar()
    bus.parar()
    await tarefa_bus
