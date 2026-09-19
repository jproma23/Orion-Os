"""Testes do registrador de experiencias (aprendizado por experiencia, camada 6).

O produtor fecha o buraco da camada 1: a tabela `experiencias` existia com o
formato exato do aprendizado do dono, mas NENHUM modulo escrevia nela - o
portao "Eu ja sei?" so lia conceitos, e um acervo vazio responde nada.

Os dois riscos que estes testes guardam (as mesmas duas formas de errar do
diario):
  1. nao gravar nada - assina o evento errado, le o campo errado, desiste;
  2. gravar demais - `navigation.obstacle_avoided` dispara a cada obstaculo
     e replanejar empilha eventos; a janela por familia impede o despejo.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta

import pytest

from motion_core.memory.experiencias import (
    CATEGORIA,
    FAMILIA_FOLLOW,
    FAMILIA_OBSTACULO,
    JANELA_SILENCIO_S,
    RESULTADO_FALHOU,
    RESULTADO_FUNCIONOU,
    RESULTADO_RISCO,
    RegistradorExperiencias,
)
from motion_core.memory.schema import MIGRACOES
from orion.kernel.event_bus import EventBus

AGORA = datetime(2026, 8, 9, 15, 0, 0)


def _colunas_reais_da_tabela(tabela: str) -> set[str]:
    """As colunas que a tabela tem DE VERDADE, tiradas do schema de producao.

    Aplica as migracoes reais num SQLite em memoria e pergunta ao proprio
    banco. Nao ha lista de nomes escrita aqui de proposito: uma lista a mais
    seria mais um lugar para divergir do schema, que e exatamente o defeito
    que estes testes passaram a guardar.
    """
    conexao = sqlite3.connect(":memory:")
    try:
        for _versao, sql in MIGRACOES:
            conexao.executescript(sql)
        return {linha[1] for linha in conexao.execute(f"PRAGMA table_info({tabela})")}
    finally:
        conexao.close()


class _MemoriaFalsa:
    """Dube da MemoryAPI que recusa o que o banco real recusaria.

    ELE ERA PERMISSIVO DEMAIS (achado de 2026-09-19). O `saber_guardar` de
    verdade valida os nomes das colunas contra o schema e levanta
    ErroColunaInvalida; este dube aceitava qualquer dicionario. Resultado: os
    cinco pontos de captura escreviam `acontecimento`/`funcionou`/`falhou`, a
    tabela sempre teve `o_que_aconteceu`/`o_que_funcionou`/`o_que_falhou`, a
    suite inteira ficou verde e a producao descartou 100% das experiencias por
    semanas. Um teste que aceita mais que a producao nao prova nada.
    """

    def __init__(self) -> None:
        self.gravados: list[dict] = []
        self.explodir = False
        self._colunas = _colunas_reais_da_tabela(CATEGORIA)

    async def saber_guardar(self, categoria: str, dados: dict) -> int:
        if self.explodir:
            raise RuntimeError("banco fora do ar")
        assert categoria == CATEGORIA
        invalidas = set(dados) - self._colunas
        assert not invalidas, (
            f"coluna(s) inexistente(s) em '{categoria}': {sorted(invalidas)} - "
            f"a tabela real tem {sorted(self._colunas)}"
        )
        self.gravados.append(dict(dados))
        return len(self.gravados)


def _registrador(bus: EventBus, memoria: _MemoriaFalsa, relogio: dict) -> RegistradorExperiencias:
    return RegistradorExperiencias(bus, memoria, agora=lambda: relogio["agora"])


def _rodar_bus(bus: EventBus) -> asyncio.Task:
    t = asyncio.create_task(bus.iniciar())
    return t


async def _parar(bus: EventBus, tarefa: asyncio.Task) -> None:
    bus.parar()
    await tarefa


@pytest.mark.asyncio
async def test_obstaculo_evitado_vira_experiencia_de_falha() -> None:
    bus = EventBus()
    tarefa = _rodar_bus(bus)
    memoria = _MemoriaFalsa()
    _registrador(bus, memoria, {"agora": AGORA})

    await bus.publish(
        "navigation.obstacle_avoided",
        {"contexto": "GOTO_sala", "motivo": "obstaculo_frontal", "distancia_frontal_cm": 25.0},
    )
    await bus.aguardar_fila_vazia()

    assert len(memoria.gravados) == 1
    dados = memoria.gravados[0]
    assert dados["situacao"] == "avancar"
    assert dados["o_que_falhou"] == "obstaculo a 25 cm"
    assert dados["resultado"] == RESULTADO_FALHOU
    assert dados["contexto"] == "motivo:obstaculo_frontal"

    await _parar(bus, tarefa)


@pytest.mark.asyncio
async def test_frente_desconhecida_tambem_e_falha_de_avanco() -> None:
    bus = EventBus()
    tarefa = _rodar_bus(bus)
    memoria = _MemoriaFalsa()
    _registrador(bus, memoria, {"agora": AGORA})

    await bus.publish(
        "navigation.obstacle_avoided",
        {"contexto": "EXPLORE", "motivo": "frente_desconhecida", "distancia_frontal_cm": None},
    )
    await bus.aguardar_fila_vazia()

    assert memoria.gravados[0]["o_que_falhou"] == "frente obstruida"
    assert memoria.gravados[0]["contexto"] == "motivo:frente_desconhecida"

    await _parar(bus, tarefa)


@pytest.mark.asyncio
async def test_alvo_perdido_vira_experiencia_de_follow() -> None:
    bus = EventBus()
    tarefa = _rodar_bus(bus)
    memoria = _MemoriaFalsa()
    _registrador(bus, memoria, {"agora": AGORA})

    await bus.publish("navigation.target_lost", {})
    await bus.aguardar_fila_vazia()

    assert len(memoria.gravados) == 1
    dados = memoria.gravados[0]
    assert dados["situacao"] == "seguir_pessoa"
    assert dados["resultado"] == RESULTADO_FALHOU
    assert "perdeu o alvo" in dados["o_que_falhou"]

    await _parar(bus, tarefa)


@pytest.mark.asyncio
async def test_erro_de_comando_vira_experiencia_com_a_acao() -> None:
    bus = EventBus()
    tarefa = _rodar_bus(bus)
    memoria = _MemoriaFalsa()
    _registrador(bus, memoria, {"agora": AGORA})

    await bus.publish("navigation.error", {"acao": "GOTO", "motivo": "comando_invalido"})
    await bus.aguardar_fila_vazia()

    dados = memoria.gravados[0]
    assert dados["situacao"] == "GOTO"
    assert dados["o_que_falhou"] == "comando_invalido"
    assert dados["resultado"] == RESULTADO_FALHOU
    assert dados["contexto"] == "acao:GOTO"

    await _parar(bus, tarefa)


@pytest.mark.asyncio
async def test_risco_assumido_vira_experiencia_de_risco() -> None:
    bus = EventBus()
    tarefa = _rodar_bus(bus)
    memoria = _MemoriaFalsa()
    _registrador(bus, memoria, {"agora": AGORA})

    await bus.publish(
        "navigation.risco_assumido",
        {"contexto": "EXPLORE", "motivo": "frente_desconhecida", "medida_confiavel": False, "angulos_varridos": 10},
    )
    await bus.aguardar_fila_vazia()

    dados = memoria.gravados[0]
    assert dados["situacao"] == "avancar_sem_medida (EXPLORE)"
    assert dados["resultado"] == RESULTADO_RISCO
    assert dados["contexto"] == "motivo:frente_desconhecida;angulos_varridos:10"

    await _parar(bus, tarefa)


@pytest.mark.asyncio
async def test_trecho_concluido_vira_experiencia_de_sucesso() -> None:
    bus = EventBus()
    tarefa = _rodar_bus(bus)
    memoria = _MemoriaFalsa()
    _registrador(bus, memoria, {"agora": AGORA})

    await bus.publish("navigation.segment_completed", {"graus": 0.0, "distancia_cm": 120.0})
    await bus.aguardar_fila_vazia()

    dados = memoria.gravados[0]
    assert dados["situacao"] == "mover"
    assert dados["o_que_funcionou"] == "trecho concluido sem obstaculo"
    assert dados["resultado"] == RESULTADO_FUNCIONOU

    await _parar(bus, tarefa)


@pytest.mark.asyncio
async def test_repeticao_da_mesma_familia_na_janela_nao_vira_registro_novo() -> None:
    bus = EventBus()
    tarefa = _rodar_bus(bus)
    memoria = _MemoriaFalsa()
    relogio = {"agora": AGORA}
    _registrador(bus, memoria, relogio)

    evento = {"contexto": "GOTO_sala", "motivo": "obstaculo_frontal", "distancia_frontal_cm": 25.0}
    await bus.publish("navigation.obstacle_avoided", evento)
    await bus.aguardar_fila_vazia()
    relogio["agora"] = AGORA + timedelta(seconds=30)
    await bus.publish("navigation.obstacle_avoided", evento)
    await bus.aguardar_fila_vazia()

    assert len(memoria.gravados) == 1, "replanejar na mesma janela nao pode despejar registros"

    await _parar(bus, tarefa)


@pytest.mark.asyncio
async def test_apos_a_janela_a_mesma_familia_vira_episodio_novo() -> None:
    bus = EventBus()
    tarefa = _rodar_bus(bus)
    memoria = _MemoriaFalsa()
    relogio = {"agora": AGORA}
    _registrador(bus, memoria, relogio)

    evento = {"contexto": "GOTO_sala", "motivo": "obstaculo_frontal", "distancia_frontal_cm": 25.0}
    await bus.publish("navigation.obstacle_avoided", evento)
    await bus.aguardar_fila_vazia()
    relogio["agora"] = AGORA + timedelta(seconds=JANELA_SILENCIO_S + 10)
    await bus.publish("navigation.obstacle_avoided", evento)
    await bus.aguardar_fila_vazia()

    assert len(memoria.gravados) == 2

    await _parar(bus, tarefa)


@pytest.mark.asyncio
async def test_familias_diferentes_sao_episodios_independentes() -> None:
    bus = EventBus()
    tarefa = _rodar_bus(bus)
    memoria = _MemoriaFalsa()
    _registrador(bus, memoria, {"agora": AGORA})

    await bus.publish(
        "navigation.obstacle_avoided",
        {"contexto": "GOTO_sala", "motivo": "obstaculo_frontal", "distancia_frontal_cm": 25.0},
    )
    await bus.publish("navigation.target_lost", {})
    await bus.aguardar_fila_vazia()

    assert len(memoria.gravados) == 2
    assert memoria.gravados[0]["resultado"] == RESULTADO_FALHOU
    assert memoria.gravados[1]["resultado"] == RESULTADO_FALHOU

    await _parar(bus, tarefa)


@pytest.mark.asyncio
async def test_falha_de_memoria_nao_derruba_o_registrador() -> None:
    bus = EventBus()
    tarefa = _rodar_bus(bus)
    memoria = _MemoriaFalsa()
    memoria.explodir = True
    _registrador(bus, memoria, {"agora": AGORA})

    # Um banco fora do ar nao pode derrubar o robo inteiro no meio da navegacao.
    await bus.publish(
        "navigation.obstacle_avoided",
        {"contexto": "GOTO_sala", "motivo": "obstaculo_frontal", "distancia_frontal_cm": 25.0},
    )
    await bus.aguardar_fila_vazia()

    assert memoria.gravados == []

    await _parar(bus, tarefa)