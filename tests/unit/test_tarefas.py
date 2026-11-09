"""orion.kernel.tarefas: tarefa em segundo plano nao some calada."""
import asyncio
import gc
import logging

import pytest

from orion.kernel import tarefas


@pytest.mark.asyncio
async def test_tarefa_fica_viva_ate_acabar():
    feito = asyncio.Event()

    async def trabalho():
        await asyncio.sleep(0.01)
        feito.set()

    tarefa = tarefas.disparar(trabalho(), "teste")
    del tarefa
    gc.collect()
    await asyncio.wait_for(feito.wait(), timeout=1.0)
    await asyncio.sleep(0)
    assert not tarefas._VIVAS


@pytest.mark.asyncio
async def test_erro_da_tarefa_vai_para_o_log(caplog):
    async def quebra():
        raise ValueError("bum")

    with caplog.at_level(logging.ERROR, logger="orion.kernel.tarefas"):
        tarefa = tarefas.disparar(quebra(), "quebradora")
        with pytest.raises(ValueError):
            await tarefa
        await asyncio.sleep(0)
    assert "quebradora" in caplog.text
    assert "bum" in caplog.text
