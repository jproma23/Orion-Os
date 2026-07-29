"""Testes da Sentinela de visão (Cap 8; EDR-0020, EDR-0023).

O arquivo nasceu em 2026-07-29 por causa de um bug que nenhum teste podia
pegar: `sentinela.capturar_foto` era publicado pelo Pi e NÃO tinha assinante
em nenhuma das duas máquinas. Os testes existentes exercitavam funções, e o
defeito morava na fiação. Por isso o teste principal aqui não verifica o que
`_pedir_foto` devolve - verifica que um pedido publicado no barramento chega
ao disco.
"""
import asyncio

import pytest

from orion.kernel.event_bus import EventBus
from orion.vision.sentinela_visao import SentinelaVisao


def _montar(tmp_path, bus, salvar_foto=None, cooldown_s=10.0):
    sentinela = SentinelaVisao(
        bus,
        cooldown_s=cooldown_s,
        pasta_fotos=str(tmp_path / "fotos"),
        salvar_foto=salvar_foto,
    )
    sentinela.registrar()
    return sentinela


@pytest.mark.asyncio
async def test_pedido_de_foto_do_barramento_chega_ao_disco(tmp_path):
    """O bug de 2026-07-29: ninguém assinava `sentinela.capturar_foto`."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    gravadas = []

    def salvar(caminho):
        gravadas.append(caminho)
        return caminho

    _montar(tmp_path, bus, salvar_foto=salvar)

    await bus.publish("sentinela.capturar_foto", {"motivo": "vigilia"})
    await bus.aguardar_fila_vazia()

    assert len(gravadas) == 1
    assert "vigilia" in gravadas[0], "o motivo tem que sobreviver no nome do arquivo"
    assert gravadas[0].endswith(".jpg")

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_pedido_de_foto_ignora_cooldown_e_pausa(tmp_path):
    """Cooldown e pausa são política de ALERTA. Um pedido explícito de foto
    não é alerta - recusá-lo seria ignorar uma ordem por uma regra que trata
    de outro assunto."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    gravadas = []
    sentinela = _montar(
        tmp_path, bus, salvar_foto=lambda c: gravadas.append(c) or c, cooldown_s=3600.0
    )
    sentinela.pausar()

    for _ in range(3):
        await bus.publish("sentinela.capturar_foto", {"motivo": "decisao_da_ia"})
    await bus.aguardar_fila_vazia()

    assert len(gravadas) == 3

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_sem_frame_nao_derruba_o_handler(tmp_path):
    """Câmera recém-aberta devolve None. Isso não pode virar exceção dentro do
    Event Bus - um handler que levanta prejudica os outros assinantes."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    _montar(tmp_path, bus, salvar_foto=lambda _c: None)

    await bus.publish("sentinela.capturar_foto", {"motivo": "sem_camera"})
    await bus.aguardar_fila_vazia()

    # sem assert de valor: o que se prova é que o barramento sobreviveu
    marcador = []
    bus.subscribe("teste.vivo", lambda e: marcador.append(1))
    await bus.publish("teste.vivo", {})
    await bus.aguardar_fila_vazia()
    assert marcador == [1]

    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_pedido_de_foto_nao_publica_nada_de_volta(tmp_path):
    """Anunciar `sentinela.foto_salva` seria repetir o bug que este handler
    corrige (tópico sem assinante). Pior: se voltasse como `sentinela.alerta`,
    a Vigília pediria foto outra vez, em laço."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    publicados = []
    original = bus.publish

    async def espiao(topico, dados=None, **kwargs):
        publicados.append(topico)
        return await original(topico, dados, **kwargs)

    _montar(tmp_path, bus, salvar_foto=lambda c: c)
    bus.publish = espiao

    await espiao("sentinela.capturar_foto", {"motivo": "vigilia"})
    await bus.aguardar_fila_vazia()

    assert publicados == ["sentinela.capturar_foto"]

    bus.publish = original
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_alerta_de_rosto_desconhecido_continua_com_o_prefixo_antigo(tmp_path):
    """O `prefixo` novo não pode ter mudado o nome das fotos de estranho, que
    é o que a pasta de produção já contém."""
    bus = EventBus()
    tarefa = asyncio.create_task(bus.iniciar())
    gravadas = []
    alertas = []
    bus.subscribe("sentinela.alerta", lambda e: alertas.append(e.dados))
    _montar(tmp_path, bus, salvar_foto=lambda c: gravadas.append(c) or c)

    await bus.publish("vision.faces_desconhecidas", {"quantidade": 2})
    await bus.aguardar_fila_vazia()

    assert len(gravadas) == 1
    assert "estranho_" in gravadas[0]
    assert alertas[0]["desconhecidos"] == 2

    bus.parar()
    await tarefa
