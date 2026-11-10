"""Testes da ponte do CHAT POR TEXTO (lado Notebook), ligada em 2026-08-03.

Mesmo padrao (e mesmo andaime) de test_ponte_conselho_mission.py: dois Event
Buses de verdade ligados por um par de transportes, para o teste exercitar o
caminho de rede inteiro e nao so a funcao.

O que protegem: quem digita esta OLHANDO a tela. Toda falha aqui tem que virar
resposta, nunca silencio - o Pi fica esperando um comm.request de verdade, e
timeout nesse caso e uma tela parada sem explicacao nenhuma.
"""
import asyncio

import pytest

from conftest import FakeTransporte
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus
from orion.mission.ponte_conversa import LIMITE_CARACTERES, PonteConversa


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


class AtendenteFalso:
    """Faz o papel do `processar_comando` do conversar_jarvis.py."""

    def __init__(self, resposta: str = "Ja estou indo!") -> None:
        self._resposta = resposta
        self.recebidos: list[str] = []

    async def __call__(self, texto: str) -> str:
        self.recebidos.append(texto)
        return self._resposta


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
async def test_texto_digitado_chega_ao_mesmo_processar_comando_da_voz():
    """O teste que importa: o texto entra INTEIRO no atendente (que na vida
    real e o `processar_comando` da voz) e a resposta dele volta para a tela.

    Se algum dia alguem criar um caminho paralelo para o texto, este teste
    continua passando - por isso o assert e sobre o que o atendente RECEBEU,
    e nao so sobre o que voltou."""
    ambiente = await _preparar()
    _, bus_notebook, _, _, servico_pi, servico_notebook = ambiente

    atendente = AtendenteFalso("Ja estou indo para a cozinha!")
    PonteConversa(atendente, servico_notebook).registrar(bus_notebook)

    resposta = await servico_pi.request(
        "mission_core",
        {"comando": "mission.conversa", "texto": "vai na cozinha"},
        timeout_s=2,
    )

    assert resposta.payload["ok"] is True
    assert resposta.payload["resposta"] == "Ja estou indo para a cozinha!"
    assert atendente.recebidos == ["vai na cozinha"]

    await _encerrar(*ambiente)


@pytest.mark.asyncio
async def test_mensagem_vazia_e_recusada_sem_gastar_chamada_de_modelo():
    ambiente = await _preparar()
    _, bus_notebook, _, _, servico_pi, servico_notebook = ambiente

    atendente = AtendenteFalso()
    PonteConversa(atendente, servico_notebook).registrar(bus_notebook)

    resposta = await servico_pi.request(
        "mission_core", {"comando": "mission.conversa", "texto": "   "}, timeout_s=2
    )

    assert resposta.payload["ok"] is False
    # o ponto do teste: nao basta responder erro, nao pode ter CHAMADO a IA
    assert atendente.recebidos == []

    await _encerrar(*ambiente)


@pytest.mark.asyncio
async def test_texto_gigante_e_recusado_por_quem_recebe():
    """O `maxlength` do formulario e sugestao: qualquer cliente pode mandar o
    que quiser neste endereco. Quem garante o limite e quem recebe."""
    ambiente = await _preparar()
    _, bus_notebook, _, _, servico_pi, servico_notebook = ambiente

    atendente = AtendenteFalso()
    PonteConversa(atendente, servico_notebook).registrar(bus_notebook)

    resposta = await servico_pi.request(
        "mission_core",
        {"comando": "mission.conversa", "texto": "a" * (LIMITE_CARACTERES + 1)},
        timeout_s=2,
    )

    assert resposta.payload["ok"] is False
    assert atendente.recebidos == []

    await _encerrar(*ambiente)


@pytest.mark.asyncio
async def test_falha_do_atendente_vira_resposta_e_nao_silencio():
    """Sem isto, uma excecao no planner deixaria quem digitou olhando uma tela
    parada ate o timeout - e o timeout nao diz o que aconteceu."""
    ambiente = await _preparar()
    _, bus_notebook, _, _, servico_pi, servico_notebook = ambiente

    async def atendente_com_falha(texto: str) -> str:
        raise RuntimeError("OpenRouter fora do ar (simulado)")

    PonteConversa(atendente_com_falha, servico_notebook).registrar(bus_notebook)

    resposta = await servico_pi.request(
        "mission_core", {"comando": "mission.conversa", "texto": "oi"}, timeout_s=2
    )

    assert resposta.payload["ok"] is False
    assert resposta.payload["erro"]

    await _encerrar(*ambiente)


@pytest.mark.asyncio
async def test_outro_comando_nao_e_respondido_por_esta_ponte():
    """Todas as pontes escutam o MESMO topico (`comm.mensagem.command`) e se
    distinguem pelo campo `comando`. Uma ponte que responde ao comando alheio
    rouba a resposta de outra."""
    ambiente = await _preparar()
    _, bus_notebook, _, _, servico_pi, servico_notebook = ambiente

    PonteConversa(AtendenteFalso(), servico_notebook).registrar(bus_notebook)

    with pytest.raises(Exception):
        await servico_pi.request(
            "mission_core", {"comando": "mission.decidir", "estado": {}}, timeout_s=0.3
        )

    await _encerrar(*ambiente)


# ---------------------------------------------------------------------------
# A1 (auditoria 2026-11-09): atender a conversa DENTRO do handler prendia o
# Event Bus do Notebook enquanto a IA pensava.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversa_lenta_nao_prende_o_bus_e_a_resposta_ainda_sai():
    import time

    ambiente = await _preparar()
    _, bus_notebook, _, _, servico_pi, servico_notebook = ambiente

    async def atendente_lento(texto: str) -> str:
        await asyncio.sleep(2.0)  # = processar_comando com o Gemini
        return "pronto"

    PonteConversa(atendente_lento, servico_notebook).registrar(bus_notebook)
    entregas: dict[str, float] = {}
    t0 = time.monotonic()
    bus_notebook.subscribe(
        "comm.mensagem.heartbeat", lambda ev: entregas.setdefault("hb", time.monotonic() - t0)
    )

    pedido = asyncio.create_task(
        servico_pi.request(
            "mission_core", {"comando": "mission.conversa", "texto": "oi"}, timeout_s=5
        )
    )
    await asyncio.sleep(0.1)
    publicado_em = time.monotonic() - t0
    await bus_notebook.publish("comm.mensagem.heartbeat", {"origem": "motion_core"})
    await asyncio.sleep(0.2)

    assert "hb" in entregas and entregas["hb"] - publicado_em < 0.2
    resposta = await pedido
    assert resposta.payload == {"ok": True, "resposta": "pronto"}

    await _encerrar(*ambiente)


@pytest.mark.asyncio
async def test_duas_mensagens_seguidas_sao_atendidas_na_ordem():
    """Fora do bus, mas ainda uma de cada vez: duas mensagens digitadas em
    seguida nao podem embaralhar o historico da conversa."""
    ambiente = await _preparar()
    _, bus_notebook, _, _, servico_pi, servico_notebook = ambiente
    ordem: list[str] = []
    dentro = 0
    pico = 0

    async def atendente(texto: str) -> str:
        nonlocal dentro, pico
        dentro += 1
        pico = max(pico, dentro)
        await asyncio.sleep(0.1 if texto == "primeira" else 0.01)
        ordem.append(texto)
        dentro -= 1
        return texto

    PonteConversa(atendente, servico_notebook).registrar(bus_notebook)
    def _pedir(texto: str):
        return servico_pi.request(
            "mission_core", {"comando": "mission.conversa", "texto": texto}, timeout_s=2
        )

    r1 = asyncio.create_task(_pedir("primeira"))
    await asyncio.sleep(0.02)
    r2 = asyncio.create_task(_pedir("segunda"))
    await asyncio.gather(r1, r2)

    assert ordem == ["primeira", "segunda"]
    assert pico == 1

    await _encerrar(*ambiente)
