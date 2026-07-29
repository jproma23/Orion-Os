"""Testes de `resolver_contexto` - o montador de contexto pode ser assincrono.

Existe porque o contexto do Pi passou a LER O BANCO (as observações do diário)
e leitura de banco é `async`. Antes disto o montador tinha de ser síncrono, e
era exatamente por isso que o diário NUNCA chegava à IA estratégica: não havia
como esperar por ele. Ela ouvia "o diário está VAZIO" todo ciclo, com o diário
gravando.
"""
from __future__ import annotations

import pytest

from motion_core.behavior.comportamento import resolver_contexto


@pytest.mark.asyncio
async def test_montador_sincrono_continua_valendo():
    """Todos os montadores que já existem são síncronos - não podem quebrar."""
    assert await resolver_contexto(lambda: "corpo: ok") == "corpo: ok"


@pytest.mark.asyncio
async def test_montador_assincrono_e_aguardado():
    async def montar():
        return "corpo: ok\nobservacoes: vi a Marah"

    assert "Marah" in await resolver_contexto(montar)


@pytest.mark.asyncio
async def test_sem_montador_devolve_vazio():
    assert await resolver_contexto(None) == ""


@pytest.mark.asyncio
async def test_none_do_montador_vira_string_vazia():
    """Nunca devolve None: quem chama concatena o resultado num prompt."""
    assert await resolver_contexto(lambda: None) == ""
