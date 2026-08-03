"""A conversa passa a ter dono - e erra para o lado seguro.

As 152 conversas do banco tinham `pessoa_id = NULL`. O que estes testes
protegem nao e "conseguir atribuir", e o CONTRARIO: nunca atribuir a pessoa
errada, porque um fato com dono errado e o robo afirmando algo falso sobre
alguem da familia.
"""
from __future__ import annotations

import pytest

from orion.kernel.event_bus import EventBus
from orion.mission.quem_fala import QuemEstaFalando


class MemoriaFalsa:
    def __init__(self, pessoas=None) -> None:
        self.pessoas = pessoas if pessoas is not None else [
            {"id": 1, "nome": "Joao Paulo"},
            {"id": 2, "nome": "Marah"},
        ]
        self.consultas = 0

    async def recall(self, categoria, filtro=None, limite=20):
        self.consultas += 1
        return list(self.pessoas)


class _Relogio:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _montar(memoria=None, validade_s=60.0):
    bus = EventBus()
    relogio = _Relogio()
    quem = QuemEstaFalando(bus, memoria or MemoriaFalsa(), validade_s, relogio)
    return bus, quem, relogio


async def _reconhecer(quem, nome):
    class _Ev:
        dados = {"nome": nome}

    await quem._ao_reconhecer(_Ev())


@pytest.mark.asyncio
async def test_sem_ninguem_reconhecido_e_conversa_da_casa():
    """O estado inicial e o de hoje: pessoa_id None. Nao inventa dono."""
    _, quem, _ = _montar()
    assert quem.pessoa_id() is None
    assert quem.nome is None


@pytest.mark.asyncio
async def test_rosto_reconhecido_credita_a_conversa():
    _, quem, _ = _montar()
    await _reconhecer(quem, "Joao Paulo")
    assert quem.pessoa_id() == 1
    assert quem.nome == "Joao Paulo"


@pytest.mark.asyncio
async def test_atribuicao_vence_por_tempo():
    """Atribuir errado e PIOR que nao atribuir: passada a janela, volta a None
    em vez de continuar creditando quem saiu da sala."""
    _, quem, relogio = _montar(validade_s=60.0)
    await _reconhecer(quem, "Joao Paulo")
    assert quem.pessoa_id() == 1

    relogio.t += 61.0
    assert quem.pessoa_id() is None, "deveria ter vencido"


@pytest.mark.asyncio
async def test_reconhecer_de_novo_renova_a_janela():
    _, quem, relogio = _montar(validade_s=60.0)
    await _reconhecer(quem, "Joao Paulo")
    relogio.t += 50.0
    await _reconhecer(quem, "Joao Paulo")  # apareceu de novo
    relogio.t += 50.0
    assert quem.pessoa_id() == 1, "a janela conta do ULTIMO reconhecimento"


@pytest.mark.asyncio
async def test_troca_de_pessoa_troca_o_credito_na_hora():
    """Quem chegou agora nao herda o credito de quem estava antes."""
    _, quem, _ = _montar()
    await _reconhecer(quem, "Joao Paulo")
    await _reconhecer(quem, "Marah")
    assert quem.pessoa_id() == 2


@pytest.mark.asyncio
async def test_desconhecido_nao_vira_chute():
    """Rosto reconhecido que nao esta na tabela nao pode virar o id de outro."""
    _, quem, _ = _montar()
    await _reconhecer(quem, "Fulano")
    assert quem.pessoa_id() is None


@pytest.mark.asyncio
async def test_evento_sem_nome_nao_credita():
    """`person_detected` diz que ha ALGUEM, nao QUEM. Nao serve para creditar."""
    _, quem, _ = _montar()

    class _Ev:
        dados = {}

    await quem._ao_reconhecer(_Ev())
    assert quem.pessoa_id() is None


@pytest.mark.asyncio
async def test_nao_consulta_o_banco_a_cada_quadro():
    """A visao publica reconhecimento a cada quadro; sem cache seria uma ida ao
    Pi por quadro, atravessando o link TCP."""
    memoria = MemoriaFalsa()
    _, quem, _ = _montar(memoria)
    for _ in range(30):
        await _reconhecer(quem, "Joao Paulo")
    assert memoria.consultas == 1


@pytest.mark.asyncio
async def test_banco_fora_do_ar_nao_derruba_a_conversa():
    class MemoriaQuebrada(MemoriaFalsa):
        async def recall(self, categoria, filtro=None, limite=20):
            raise RuntimeError("Pi inalcancavel (simulado)")

    _, quem, _ = _montar(MemoriaQuebrada())
    await _reconhecer(quem, "Joao Paulo")  # nao levanta
    assert quem.pessoa_id() is None


@pytest.mark.asyncio
async def test_casa_pelo_primeiro_nome():
    """A visao pode devolver so o primeiro nome; o cadastro guarda o completo."""
    _, quem, _ = _montar()
    await _reconhecer(quem, "Joao")
    assert quem.pessoa_id() == 1
