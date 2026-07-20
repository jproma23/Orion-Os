"""Testes do diário de observações (camada 2 da cognição).

O que o diário existe para resolver: até agora o bloco de observações do
grounding chegava SEMPRE vazio, porque nada nunca o preenchia. O robô era
honesto por não ter memória, não por ter olhado.

Os dois riscos que estes testes guardam:
  1. não gravar nada (volta ao estado anterior, mudo);
  2. gravar demais - `vision.person_detected` dispara a cada verificação, e
     inundar o diário encheria o prompt de repetição, que é a melhor forma
     de um modelo pequeno perder o que importa.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from orion.kernel.event_bus import EventBus, Evento
from orion.mission.diario import (
    CATEGORIA,
    MAX_OBSERVACOES_CONTEXTO,
    ORIGEM,
    TIPO_DESCONHECIDO,
    TIPO_PESSOA_VISTA,
    DiarioObservacoes,
)

AGORA = datetime(2026, 7, 19, 15, 0, 0)


class _MemoriaFalsa:
    def __init__(self, registros: list[dict] | None = None) -> None:
        self.gravados: list[tuple[str, dict]] = []
        self._registros = registros or []
        self.explodir_no_remember = False
        self.explodir_no_recall = False

    async def remember(self, categoria: str, dados: dict) -> int:
        if self.explodir_no_remember:
            raise RuntimeError("banco fora do ar")
        self.gravados.append((categoria, dados))
        return len(self.gravados)

    async def recall(self, categoria: str, filtro=None, limite: int = 20) -> list[dict]:
        if self.explodir_no_recall:
            raise RuntimeError("banco fora do ar")
        return self._registros[:limite]


def _diario(memoria: _MemoriaFalsa, agora: datetime = AGORA) -> DiarioObservacoes:
    return DiarioObservacoes(EventBus(), memoria, agora=lambda: agora)


def _registro(tipo: str, quando: datetime, **dados) -> dict:
    return {
        "origem": ORIGEM,
        "tipo": tipo,
        "payload_json": json.dumps(dados, ensure_ascii=False),
        "timestamp": quando.isoformat(),
    }


# ----- escrita -----


@pytest.mark.asyncio
async def test_pessoa_conhecida_vira_registro() -> None:
    memoria = _MemoriaFalsa()
    diario = _diario(memoria)

    await diario._ao_ver_pessoa(Evento("vision.person_detected", {"nome": "João Paulo"}))

    assert len(memoria.gravados) == 1
    categoria, dados = memoria.gravados[0]
    assert categoria == CATEGORIA
    assert dados["tipo"] == TIPO_PESSOA_VISTA
    assert "João Paulo" in dados["payload_json"]


@pytest.mark.asyncio
async def test_rosto_sem_nome_nao_grava_aqui() -> None:
    """O estranho é gravado pelo alerta da sentinela. Gravar nos dois
    lugares faria a mesma pessoa contar como duas visitas."""
    memoria = _MemoriaFalsa()
    diario = _diario(memoria)

    await diario._ao_ver_pessoa(Evento("vision.person_detected", {}))

    assert memoria.gravados == []


@pytest.mark.asyncio
async def test_alerta_de_pessoa_vira_desconhecido() -> None:
    memoria = _MemoriaFalsa()
    diario = _diario(memoria)

    await diario._ao_receber_alerta(Evento("sentinela.alerta", {"tipo": "pessoa"}))

    assert memoria.gravados[0][1]["tipo"] == TIPO_DESCONHECIDO


@pytest.mark.asyncio
async def test_alerta_que_nao_e_de_pessoa_e_ignorado() -> None:
    memoria = _MemoriaFalsa()
    diario = _diario(memoria)

    await diario._ao_receber_alerta(Evento("sentinela.alerta", {"tipo": "barulho"}))

    assert memoria.gravados == []


@pytest.mark.asyncio
async def test_mesma_pessoa_na_janela_nao_grava_de_novo() -> None:
    """O risco de inundar: a visão redetecta a cada verificação."""
    memoria = _MemoriaFalsa()
    diario = _diario(memoria)
    evento = Evento("vision.person_detected", {"nome": "Ana"})

    for _ in range(20):
        await diario._ao_ver_pessoa(evento)

    assert len(memoria.gravados) == 1


@pytest.mark.asyncio
async def test_mesma_pessoa_depois_da_janela_grava_de_novo() -> None:
    """Silêncio não é esquecimento: passada a janela, é presença nova."""
    memoria = _MemoriaFalsa()
    relogio = {"agora": AGORA}
    diario = DiarioObservacoes(
        EventBus(), memoria, janela_silencio_s=600.0, agora=lambda: relogio["agora"]
    )
    evento = Evento("vision.person_detected", {"nome": "Bruno"})

    await diario._ao_ver_pessoa(evento)
    relogio["agora"] = AGORA + timedelta(seconds=601)
    await diario._ao_ver_pessoa(evento)

    assert len(memoria.gravados) == 2


@pytest.mark.asyncio
async def test_pessoas_diferentes_tem_janelas_independentes() -> None:
    memoria = _MemoriaFalsa()
    diario = _diario(memoria)

    await diario._ao_ver_pessoa(Evento("vision.person_detected", {"nome": "Ana"}))
    await diario._ao_ver_pessoa(Evento("vision.person_detected", {"nome": "Bruno"}))

    assert len(memoria.gravados) == 2


@pytest.mark.asyncio
async def test_falha_do_banco_nao_propaga() -> None:
    """Memória fora do ar não pode derrubar a visão nem a conversa."""
    memoria = _MemoriaFalsa()
    memoria.explodir_no_remember = True
    diario = _diario(memoria)

    await diario._ao_ver_pessoa(Evento("vision.person_detected", {"nome": "Ana"}))  # não levanta


# ----- leitura -----


@pytest.mark.asyncio
async def test_sem_registros_devolve_lista_vazia() -> None:
    """Lista vazia é o que o grounding vira em 'não tenho registro hoje'."""
    assert await _diario(_MemoriaFalsa()).observacoes_de_hoje() == []


@pytest.mark.asyncio
async def test_registros_de_hoje_viram_frases_com_hora() -> None:
    memoria = _MemoriaFalsa(
        [_registro(TIPO_PESSOA_VISTA, AGORA - timedelta(hours=1), nome="João Paulo")]
    )

    observacoes = await _diario(memoria).observacoes_de_hoje()

    assert observacoes == [{"quando": "14:00", "o_que": "vi João Paulo"}]


@pytest.mark.asyncio
async def test_registro_de_ontem_nao_entra() -> None:
    """"Hoje" tem que ser hoje - senão o robô relata visita de ontem como
    se fosse agora, que é uma forma sutil de mentir."""
    memoria = _MemoriaFalsa(
        [_registro(TIPO_PESSOA_VISTA, AGORA - timedelta(days=1), nome="Ana")]
    )

    assert await _diario(memoria).observacoes_de_hoje() == []


@pytest.mark.asyncio
async def test_ordem_cronologica_na_saida() -> None:
    """recall devolve o mais recente primeiro; no texto, ordem natural."""
    memoria = _MemoriaFalsa(
        [
            _registro(TIPO_PESSOA_VISTA, AGORA - timedelta(minutes=10), nome="Bruno"),
            _registro(TIPO_PESSOA_VISTA, AGORA - timedelta(hours=2), nome="Ana"),
        ]
    )

    observacoes = await _diario(memoria).observacoes_de_hoje()

    assert [o["o_que"] for o in observacoes] == ["vi Ana", "vi Bruno"]


@pytest.mark.asyncio
async def test_limita_o_tamanho_do_bloco() -> None:
    """O bloco vai inteiro no prompt de um modelo de 1B - não pode inchar."""
    memoria = _MemoriaFalsa(
        [
            _registro(TIPO_PESSOA_VISTA, AGORA - timedelta(minutes=i), nome=f"P{i}")
            for i in range(50)
        ]
    )

    observacoes = await _diario(memoria).observacoes_de_hoje()

    assert len(observacoes) == MAX_OBSERVACOES_CONTEXTO


@pytest.mark.asyncio
async def test_desconhecido_e_descrito_como_nao_reconhecido() -> None:
    memoria = _MemoriaFalsa([_registro(TIPO_DESCONHECIDO, AGORA - timedelta(minutes=5))])

    observacoes = await _diario(memoria).observacoes_de_hoje()

    assert observacoes[0]["o_que"] == "vi uma pessoa que não reconheci"


@pytest.mark.asyncio
async def test_falha_na_leitura_devolve_vazio_em_vez_de_explodir() -> None:
    memoria = _MemoriaFalsa()
    memoria.explodir_no_recall = True

    assert await _diario(memoria).observacoes_de_hoje() == []


@pytest.mark.asyncio
async def test_registro_com_timestamp_corrompido_e_pulado() -> None:
    memoria = _MemoriaFalsa(
        [
            {"tipo": TIPO_PESSOA_VISTA, "payload_json": "{}", "timestamp": "isso nao e data"},
            _registro(TIPO_PESSOA_VISTA, AGORA - timedelta(minutes=5), nome="Ana"),
        ]
    )

    observacoes = await _diario(memoria).observacoes_de_hoje()

    assert [o["o_que"] for o in observacoes] == ["vi Ana"]
