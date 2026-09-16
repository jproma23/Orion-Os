"""Replica de backup auto-regenerativa (auto-regeneracao).

O Notebook (Mission Core) dorme - o backup das 3h nao pode ser perdido
por isso. Este modulo guarda em fila os backups que falharam e os
reenvia sozinho quando o link com o Notebook volta.

Eventos usados:
- `database.backup_completed`: dispara a replica (sem sucesso -> entra na fila)
- `comm.module_recovered`: Notebook voltou -> drena a fila de pendentes
- `comm.module_lost`: Notebook caiu -> marca para reenviar depois

A fila vive em disco (arquivo JSON), entao sobrevive a reinicios do
Motion Core: se o processo cair no meio de uma replica, os pendentes
continuam la quando ele voltar.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus, Evento, Prioridade

from motion_core.memory.replica import replicar_backup

logger = logging.getLogger("motion_core.memory.replica_pendente")

EVENTO_BACKUP = "database.backup_completed"
EVENTO_RECUPERADO = "comm.module_recovered"
EVENTO_PERDIDO = "comm.module_lost"

NOME_MODULO = "motion_core"
DESTINO = "mission_core"


class FilaReplicaPendente:
    """Guarda caminhos de backup que falharam e os reenvia sozinhos."""

    def __init__(self, caminho_fila: Path | str) -> None:
        self._caminho = Path(caminho_fila)
        self._pendentes: list[str] = []

    def carregar(self) -> None:
        if not self._caminho.exists():
            self._pendentes = []
            return
        try:
            dados = json.loads(self._caminho.read_text(encoding="utf-8"))
            self._pendentes = list(dados.get("pendentes", []))
        except (ValueError, OSError) as erro:
            logger.warning("fila de replica ilegivel (%s) - comecando vazia", erro)
            self._pendentes = []

    def salvar(self) -> None:
        self._caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = self._caminho.with_suffix(".tmp")
        temporario.write_text(
            json.dumps({"pendentes": self._pendentes}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporario, self._caminho)

    def adicionar(self, caminho: str) -> None:
        if caminho not in self._pendentes:
            self._pendentes.append(caminho)
            self.salvar()
            logger.info("replica pendente registrada: %s", caminho)

    def remover(self, caminho: str) -> None:
        if caminho in self._pendentes:
            self._pendentes.remove(caminho)
            self.salvar()

    def pendentes(self) -> list[str]:
        return list(self._pendentes)


class ReplicadorAutoRegenerativo:
    """Dispara a replica no backup e reenvia pendentes quando o Notebook volta."""

    def __init__(
        self,
        event_bus: EventBus,
        comm: ComunicacaoService,
        fila: FilaReplicaPendente,
        destino: str = DESTINO,
    ) -> None:
        self._event_bus = event_bus
        self._comm = comm
        self._fila = fila
        self._destino = destino
        self._filas_ativadas: list[asyncio.Future] = []

    def registrar(self) -> None:
        self._event_bus.subscribe(EVENTO_BACKUP, self._ao_backup_completar)
        self._event_bus.subscribe(EVENTO_RECUPERADO, self._ao_recuperado)
        self._event_bus.subscribe(EVENTO_PERDIDO, self._ao_perdido)

    def parar(self) -> None:
        for futura in self._filas_ativadas:
            futura.cancel()
        self._filas_ativadas = []

    async def _ao_backup_completar(self, evento: Evento) -> None:
        caminho = str(Path(evento.dados["arquivo"]))
        try:
            await replicar_backup(Path(caminho), self._comm, destino=self._destino)
            logger.info("Backup replicado para o Notebook: %s", Path(caminho).name)
        except Exception as erro:
            logger.warning(
                "Backup NAO replicado (%s) - fica pendente e reenvia quando o "
                "Notebook voltar: %s",
                erro,
                caminho,
            )
            self._fila.adicionar(caminho)

    async def _ao_recuperado(self, evento: Evento) -> None:
        if evento.dados.get("modulo") != self._destino:
            return
        pendentes = self._fila.pendentes()
        if not pendentes:
            return
        logger.info(
            "Notebook voltou - reenviando %d replica(s) pendente(s)",
            len(pendentes),
        )
        tarefa = asyncio.create_task(self._drenar(pendentes))
        self._filas_ativadas.append(tarefa)
        tarefa.add_done_callback(self._filas_ativadas.remove)

    async def _drenar(self, pendentes: list[str]) -> None:
        for caminho in pendentes:
            try:
                await replicar_backup(Path(caminho), self._comm, destino=self._destino)
                self._fila.remover(caminho)
                logger.info("Replica pendente enviada: %s", Path(caminho).name)
            except Exception as erro:
                logger.warning(
                    "Pendente ainda nao enviado (%s) - tenta de novo na proxima "
                    "reconexao: %s",
                    erro,
                    caminho,
                )

    async def _ao_perdido(self, evento: Evento) -> None:
        if evento.dados.get("modulo") == self._destino:
            logger.info("Notebook (Mission Core) ficou fora - replicas ficam pendentes")