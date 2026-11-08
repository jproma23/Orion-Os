"""Tarefa em segundo plano que nao some calada (2026-11-08).

`asyncio.create_task(...)` sem guardar o retorno tem dois defeitos, os dois
silenciosos. O laco de eventos so guarda referencia FRACA a tarefa: sem
ninguem segurando, ela pode ser recolhida pelo coletor de lixo no meio do
caminho (aviso da propria documentacao do Python). E se ela levantar um erro,
ninguem o le - aparece, no maximo, um "Task exception was never retrieved"
quando o processo ja esta longe dali.

A auditoria de 08/11 achou seis lugares assim, entre eles a conferencia do
movimento pelo LiDAR (corpo_agora) e o laco do enlace com o corpo
(ponte_corpo_ws). `disparar` e o jeito unico de fazer isso: guarda a tarefa
ate ela acabar e registra no log quando ela morre com erro.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger("orion.kernel.tarefas")

#: As tarefas vivas. Segurar a referencia e o que impede o coletor de lixo.
_VIVAS: set[asyncio.Task] = set()


def disparar(corrotina: Coroutine[Any, Any, Any], nome: str) -> asyncio.Task:
    """Roda `corrotina` em segundo plano, segura a tarefa e loga se ela falhar."""
    tarefa = asyncio.create_task(corrotina, name=nome)
    _VIVAS.add(tarefa)
    tarefa.add_done_callback(_ao_terminar)
    return tarefa


def _ao_terminar(tarefa: asyncio.Task) -> None:
    _VIVAS.discard(tarefa)
    if tarefa.cancelled():
        return
    erro = tarefa.exception()
    if erro is not None:
        logger.error(
            "tarefa em segundo plano '%s' morreu com erro",
            tarefa.get_name(),
            exc_info=(type(erro), erro, erro.__traceback__),
        )
