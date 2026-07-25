"""Orquestracao assincrona do Database Manager com o Event Bus (Cap 15 s.6, s.9).

`DatabaseManager` (database.py) e deliberadamente sincrono e sem
dependencia do Event Bus (mais simples de testar isoladamente). Este modulo
faz a ponte: publica os eventos oficiais do Cap 15 e agenda backup +
retencao como tarefa noturna, delegando o trabalho bloqueante do sqlite3
para uma thread (`asyncio.to_thread`) para nao travar o event loop.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from motion_core.memory.database import DatabaseManager
from orion.kernel.event_bus import EventBus, Prioridade

logger = logging.getLogger("motion_core.memory.manutencao")


async def iniciar_banco(db: DatabaseManager, event_bus: EventBus) -> None:
    """Roda DatabaseManager.iniciar() numa thread e publica o evento
    correspondente (Cap 15 s.9): database.ready, ou database.rebuilt /
    database.integrity_error quando houve recuperacao."""
    await asyncio.to_thread(db.iniciar)
    if db.foi_reconstruido:
        await event_bus.publish(
            "database.rebuilt",
            {"caminho": str(db.caminho_db)},
            prioridade=Prioridade.ALTA,
        )
    await event_bus.publish("database.ready", {"caminho": str(db.caminho_db)})


class TarefaManutencao:
    """Backup diario + retencao (Cap 15 s.5-6): roda no maximo uma vez por
    dia, no horario configurado (`database.backup_hour`, hora local)."""

    def __init__(
        self,
        db: DatabaseManager,
        event_bus: EventBus,
        hora_backup: int = 3,
        retencao_kwargs: dict | None = None,
        intervalo_verificacao_s: float = 60.0,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        self._hora_backup = hora_backup
        self._retencao_kwargs = retencao_kwargs or {}
        self._intervalo_verificacao_s = intervalo_verificacao_s
        self._ultimo_backup_data: date | None = None
        self._executando = False

    async def executar_backup_agora(self) -> None:
        """Roda backup + retencao imediatamente - usado pelo loop agendado
        e disponivel para acionamento manual/testes."""
        try:
            # Lock compartilhado com MemoryAPI (achado real da vistoria de
            # 2026-07-24): sem isso, o backup as 3h podia coincidir com uma
            # consulta pelo dashboard ou um memory.remember chegando do
            # Notebook, duas threads mexendo na mesma conexao sqlite3 ao
            # mesmo tempo.
            async with self._db.lock:
                caminho = await asyncio.to_thread(self._db.fazer_backup)
                removidos = await asyncio.to_thread(
                    self._db.limpar_retencao, **self._retencao_kwargs
                )
        except Exception as erro:
            logger.exception("Falha ao executar backup diario")
            await self._event_bus.publish(
                "database.backup_failed", {"motivo": str(erro)}, prioridade=Prioridade.ALTA
            )
            raise

        await self._event_bus.publish(
            "database.backup_completed",
            {"arquivo": str(caminho), "retencao_removidos": removidos},
        )

    async def iniciar(self) -> None:
        self._executando = True
        while self._executando:
            agora = datetime.now()
            hoje = agora.date()
            if agora.hour == self._hora_backup and self._ultimo_backup_data != hoje:
                try:
                    await self.executar_backup_agora()
                except Exception:
                    # executar_backup_agora() ja loga e publica
                    # database.backup_failed antes de relancar (Cap 15 s.9) -
                    # aqui so garantimos que uma falha de UMA noite (SSD
                    # cheio, backup_dir sem permissao momentaneamente) nao
                    # mata a task para sempre. Sem isso, nenhum backup nem
                    # limpeza de retencao rodava de novo ate reiniciar o
                    # processo (achado real da vistoria de 2026-07-24). Nao
                    # marca _ultimo_backup_data, entao tenta de novo no
                    # proximo ciclo de verificacao, ainda dentro da mesma
                    # hora configurada.
                    pass
                else:
                    self._ultimo_backup_data = hoje
            await asyncio.sleep(self._intervalo_verificacao_s)

    def parar(self) -> None:
        self._executando = False
