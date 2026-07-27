"""Testes do conselheiro de comportamento.

O que protegem: a IA NÃO pode atrapalhar. Em qualquer falha dela (lenta,
fora do ar, resposta inválida, palpite em cima da segurança) o resultado
tem que ser "seguir pela regra", nunca um comportamento errado aceito.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from orion.mission.conselheiro_comportamento import (
    COMPORTAMENTOS_DE_SEGURANCA,
    ConselheiroComportamento,
)

OPCOES = ["repouso", "atender", "vigilia", "vigilancia_obstaculo"]


class _RespostaFalsa:
    """Imita o objeto devolvido pela OpenAI, que o código lê como
    `resposta.choices[0].message.content`."""

    def __init__(self, conteudo: str | None) -> None:
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=conteudo))]


class _ClienteFalso:
    """Substitui o cliente da IA: devolve o que o teste mandar, ou explode.

    Imita a API da OpenAI/OpenRouter (`cliente.chat.completions.create`), que
    é a que o `ConselheiroComportamento` usa desde o EDR-0021. Até o merge de
    2026-07-27 este dublê imitava o Ollama (`cliente.generate`), herdado da
    versão do GitHub - o resultado era um `AttributeError` engolido pelo
    `except Exception` do `aconselhar()`, que virava "IA indisponível". Os
    testes de falha passavam por acidente (esperavam `None` e recebiam `None`
    pelo motivo errado); os de sucesso falhavam.
    """

    def __init__(self, resposta: str | None = None, erro: Exception | None = None,
                 demora_s: float = 0.0) -> None:
        self._resposta = resposta
        self._erro = erro
        self._demora_s = demora_s
        self.opcoes_recebidas: list[str] | None = None
        # Espelha a árvore de atributos do cliente real: cliente.chat.completions.create
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        import time

        if self._demora_s:
            time.sleep(self._demora_s)
        if self._erro:
            raise self._erro
        # No Ollama o enum vinha em format["properties"]; no formato estruturado
        # da OpenAI ele mora dentro de response_format["json_schema"]["schema"].
        formato = kwargs.get("response_format") or {}
        self.opcoes_recebidas = (
            formato.get("json_schema", {})
            .get("schema", {})
            .get("properties", {})
            .get("comportamento", {})
            .get("enum")
        )
        return _RespostaFalsa(self._resposta)


def _conselheiro(cliente: _ClienteFalso, timeout_s: float = 8.0) -> ConselheiroComportamento:
    """Cria o conselheiro sem falar com nenhuma IA de verdade.

    O objeto é montado sem passar pelo `__init__` porque ele lê variáveis de
    ambiente (chave de API). Injetar `_cliente` já pronto também impede que
    `_chamar()` construa um `OpenAI()` real.
    """
    c = ConselheiroComportamento.__new__(ConselheiroComportamento)
    c._modelo = "openai/gpt-4o-mini"
    c._temperatura = 0.3
    c._timeout_s = timeout_s
    c._api_key = "chave-de-teste"
    c._base_url = None
    c._cliente = cliente
    return c


@pytest.mark.asyncio
async def test_seguranca_ativa_nem_consulta_a_ia() -> None:
    """Com segurança pedindo o controle, a IA não é chamada - nem para
    opinar. Regra vence sem negociação."""
    cliente = _ClienteFalso(resposta=json.dumps({"comportamento": "repouso", "motivo": "x"}))
    conselho = await _conselheiro(cliente).aconselhar("ctx", OPCOES, seguranca_ativa=True)
    assert conselho is None
    assert cliente.opcoes_recebidas is None  # nem chegou a chamar


@pytest.mark.asyncio
async def test_comportamento_de_seguranca_nunca_e_oferecido() -> None:
    """A IA não pode escolher entrar em segurança - isso é condição física
    medida, não opinião."""
    cliente = _ClienteFalso(resposta=json.dumps({"comportamento": "vigilia", "motivo": "x"}))
    await _conselheiro(cliente).aconselhar("ctx", OPCOES)
    assert cliente.opcoes_recebidas is not None
    for proibido in COMPORTAMENTOS_DE_SEGURANCA:
        assert proibido not in cliente.opcoes_recebidas


@pytest.mark.asyncio
async def test_conselho_valido_e_aceito() -> None:
    cliente = _ClienteFalso(
        resposta=json.dumps({"comportamento": "atender", "motivo": "Ana chamou"})
    )
    conselho = await _conselheiro(cliente).aconselhar("ctx", OPCOES)
    assert conselho is not None
    assert conselho.aceito is True
    assert conselho.comportamento == "atender"
    assert conselho.motivo == "Ana chamou"


@pytest.mark.asyncio
async def test_json_invalido_e_descartado() -> None:
    conselho = await _conselheiro(_ClienteFalso(resposta="isso nao e json")).aconselhar(
        "ctx", OPCOES
    )
    assert conselho is None  # segue pela regra


@pytest.mark.asyncio
async def test_comportamento_fora_da_lista_e_recusado() -> None:
    """Cinto e suspensório: mesmo que o schema falhe, a validação pega."""
    cliente = _ClienteFalso(
        resposta=json.dumps({"comportamento": "vigilancia", "motivo": "erro tipico do 1b"})
    )
    conselho = await _conselheiro(cliente).aconselhar("ctx", OPCOES)
    assert conselho is not None
    assert conselho.aceito is False
    assert "invalido" in conselho.recusa


@pytest.mark.asyncio
async def test_ia_fora_do_ar_nao_derruba_nada() -> None:
    cliente = _ClienteFalso(erro=ConnectionError("ollama caiu"))
    assert await _conselheiro(cliente).aconselhar("ctx", OPCOES) is None


@pytest.mark.asyncio
async def test_ia_lenta_e_abandonada_no_timeout() -> None:
    """Conselho que chega tarde não serve - o maestro não espera a IA."""
    cliente = _ClienteFalso(
        resposta=json.dumps({"comportamento": "repouso", "motivo": "x"}), demora_s=0.5
    )
    conselho = await _conselheiro(cliente, timeout_s=0.1).aconselhar("ctx", OPCOES)
    assert conselho is None


@pytest.mark.asyncio
async def test_sem_opcoes_permitidas_nao_consulta() -> None:
    cliente = _ClienteFalso(resposta="{}")
    conselho = await _conselheiro(cliente).aconselhar("ctx", list(COMPORTAMENTOS_DE_SEGURANCA))
    assert conselho is None
    assert cliente.opcoes_recebidas is None
