"""Testes da ponte do Mentor de comportamento (lado Notebook), ligada em
2026-07-24. Mesmo padrao de test_decisao_estrategica.py (PonteDecisao)."""
import asyncio

import pytest

from conftest import FakeTransporte
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus
from orion.mission.conselheiro_comportamento import Conselho
from orion.mission.ponte_conselho import PonteConselho


async def _rodar_bus(bus: EventBus) -> asyncio.Task:
    return asyncio.create_task(bus.iniciar())


def _par_conectado():
    canal_a_para_b: asyncio.Queue = asyncio.Queue()
    canal_b_para_a: asyncio.Queue = asyncio.Queue()

    transporte_a = FakeTransporte()
    transporte_a._entrada = canal_b_para_a
    transporte_a.enviar = canal_a_para_b.put

    transporte_b = FakeTransporte()
    transporte_b._entrada = canal_a_para_b
    transporte_b.enviar = canal_b_para_a.put

    return transporte_a, transporte_b


class ConselheiroFalso:
    def __init__(self, conselho: Conselho | None) -> None:
        self._conselho = conselho
        self.chamadas: list[tuple[str, list[str], bool]] = []

    async def aconselhar(self, contexto_texto, opcoes, seguranca_ativa=False):
        self.chamadas.append((contexto_texto, opcoes, seguranca_ativa))
        return self._conselho


async def _preparar():
    bus_pi = EventBus()
    bus_notebook = EventBus()
    tarefa_pi = await _rodar_bus(bus_pi)
    tarefa_notebook = await _rodar_bus(bus_notebook)

    servico_pi = ComunicacaoService("motion_core", bus_pi)
    servico_notebook = ComunicacaoService("mission_core", bus_notebook)
    transporte_pi, transporte_notebook = _par_conectado()
    servico_pi.adicionar_link("mission_core", transporte_pi)
    servico_notebook.adicionar_link("motion_core", transporte_notebook)

    return bus_pi, bus_notebook, tarefa_pi, tarefa_notebook, servico_pi, servico_notebook


async def _encerrar(bus_pi, bus_notebook, tarefa_pi, tarefa_notebook, servico_pi, servico_notebook):
    await servico_pi.encerrar()
    await servico_notebook.encerrar()
    bus_pi.parar()
    bus_notebook.parar()
    await tarefa_pi
    await tarefa_notebook


@pytest.mark.asyncio
async def test_behavior_aconselhar_via_comm_request():
    bus_pi, bus_notebook, tarefa_pi, tarefa_notebook, servico_pi, servico_notebook = (
        await _preparar()
    )

    conselheiro = ConselheiroFalso(
        Conselho(comportamento="observar_ambiente", motivo="tudo calmo", aceito=True)
    )
    ponte = PonteConselho(conselheiro, servico_notebook)
    ponte.registrar(bus_notebook)

    resposta = await servico_pi.request(
        "mission_core",
        {
            "comando": "behavior.aconselhar",
            "contexto": "{'comportamento_ativo': 'repouso'}",
            "opcoes": ["descansar", "observar_ambiente"],
            "seguranca_ativa": False,
        },
        timeout_s=2,
    )

    assert resposta.payload["ok"] is True
    assert resposta.payload["comportamento"] == "observar_ambiente"
    assert resposta.payload["motivo"] == "tudo calmo"
    assert conselheiro.chamadas == [
        ("{'comportamento_ativo': 'repouso'}", ["descansar", "observar_ambiente"], False)
    ]

    await _encerrar(bus_pi, bus_notebook, tarefa_pi, tarefa_notebook, servico_pi, servico_notebook)


@pytest.mark.asyncio
async def test_sem_conselho_aceito_retorna_ok_false():
    bus_pi, bus_notebook, tarefa_pi, tarefa_notebook, servico_pi, servico_notebook = (
        await _preparar()
    )

    ponte = PonteConselho(ConselheiroFalso(None), servico_notebook)
    ponte.registrar(bus_notebook)

    resposta = await servico_pi.request(
        "mission_core",
        {"comando": "behavior.aconselhar", "contexto": "", "opcoes": ["descansar"]},
        timeout_s=2,
    )

    assert resposta.payload["ok"] is False

    await _encerrar(bus_pi, bus_notebook, tarefa_pi, tarefa_notebook, servico_pi, servico_notebook)


@pytest.mark.asyncio
async def test_falha_do_conselheiro_nao_derruba_e_responde_ok_false():
    """Achado real (mesma classe do que ja foi corrigido hoje em
    _validar): a ponte nao pode deixar uma excecao do conselheiro
    derrubar o Notebook nem deixar o Pi esperando sem resposta."""
    bus_pi, bus_notebook, tarefa_pi, tarefa_notebook, servico_pi, servico_notebook = (
        await _preparar()
    )

    class ConselheiroComFalha:
        async def aconselhar(self, *args, **kwargs):
            raise RuntimeError("Ollama local fora do ar (simulado)")

    ponte = PonteConselho(ConselheiroComFalha(), servico_notebook)
    ponte.registrar(bus_notebook)

    resposta = await servico_pi.request(
        "mission_core",
        {"comando": "behavior.aconselhar", "contexto": "", "opcoes": ["descansar"]},
        timeout_s=2,
    )

    assert resposta.payload["ok"] is False

    await _encerrar(bus_pi, bus_notebook, tarefa_pi, tarefa_notebook, servico_pi, servico_notebook)


@pytest.mark.asyncio
async def test_comando_diferente_e_ignorado_pela_ponte():
    bus_pi, bus_notebook, tarefa_pi, tarefa_notebook, servico_pi, servico_notebook = (
        await _preparar()
    )

    ponte = PonteConselho(ConselheiroFalso(None), servico_notebook)
    ponte.registrar(bus_notebook)

    with pytest.raises(Exception):
        await servico_pi.request(
            "mission_core", {"comando": "mission.decidir", "estado": {}}, timeout_s=0.3
        )

    await _encerrar(bus_pi, bus_notebook, tarefa_pi, tarefa_notebook, servico_pi, servico_notebook)


# ---------------------------------------------------------------------------
# A1 (auditoria 2026-11-09): o Event Bus despacha um evento por vez, e a
# ponte esperava a IA DENTRO do handler - medido: visao e heartbeat do
# Notebook publicados em t=0,1 s so chegavam em 2,02 s com uma IA de 2 s.
# ---------------------------------------------------------------------------


class _ConselheiroLento(ConselheiroFalso):
    def __init__(self, segundos: float) -> None:
        super().__init__(Conselho(comportamento="esperar", motivo="lento", aceito=True))
        self._segundos = segundos

    async def aconselhar(self, contexto_texto, opcoes, seguranca_ativa=False):
        await asyncio.sleep(self._segundos)  # = chamada ao Gemini
        return await super().aconselhar(contexto_texto, opcoes, seguranca_ativa)


@pytest.mark.asyncio
async def test_ia_lenta_nao_prende_o_bus_do_notebook_e_a_resposta_ainda_sai():
    import time

    from orion.kernel.event_bus import Prioridade

    ambiente = await _preparar()
    bus_pi, bus_notebook, tarefa_pi, tarefa_notebook, servico_pi, servico_notebook = ambiente
    PonteConselho(_ConselheiroLento(2.0), servico_notebook).registrar(bus_notebook)

    entregas: dict[str, float] = {}
    t0 = time.monotonic()
    bus_notebook.subscribe(
        "vision.person_detected", lambda ev: entregas.setdefault("visao", time.monotonic() - t0)
    )

    pedido = asyncio.create_task(
        servico_pi.request(
            "mission_core",
            {"comando": "behavior.aconselhar", "contexto": "perdi a pessoa", "opcoes": ["esperar"]},
            timeout_s=5,
        )
    )
    await asyncio.sleep(0.1)
    publicado_em = time.monotonic() - t0
    await bus_notebook.publish(
        "vision.person_detected", {"classe": "person"}, prioridade=Prioridade.CRITICA
    )
    await asyncio.sleep(0.2)

    assert "visao" in entregas, "a visao ficou presa atras da IA"
    assert entregas["visao"] - publicado_em < 0.2

    resposta = await pedido  # a RESPONSE ainda sai, depois da IA pensar
    assert resposta.payload["ok"] is True
    assert resposta.payload["comportamento"] == "esperar"

    await _encerrar(*ambiente)
