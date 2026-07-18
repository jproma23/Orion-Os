"""Replica cruzada do backup para o Notebook (Cap 15 secao 6).

Backup diario copiado ao Notebook pela rede: se o SSD do Raspberry falhar,
a memoria do robo sobrevive no Notebook (e vice-versa). Transferido em
blocos via comm.send (COMMAND com ACK e retransmissao, Cap 14 s.5) - reusa
o protocolo existente em vez de inventar um transporte de arquivos separado.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("motion_core.memory.replica")

COMANDO_CHUNK = "backup_replica_chunk"
TAMANHO_CHUNK_PADRAO = 32_768


async def replicar_backup(
    caminho_backup: Path,
    servico: ComunicacaoService,
    destino: str = "mission_core",
    tamanho_chunk: int = TAMANHO_CHUNK_PADRAO,
) -> int:
    """Envia `caminho_backup` em blocos para `destino`. Retorna o numero de
    blocos enviados.

    Cada bloco usa comm.send (ACK + ate 3 retransmissoes, Cap 14 s.5) - se
    algum bloco falhar em definitivo, a excecao `ErroComunicacao` sobe para
    quem chamou decidir o que fazer (ex.: tentar de novo no proximo backup).
    """
    dados = caminho_backup.read_bytes()
    blocos = [dados[i : i + tamanho_chunk] for i in range(0, len(dados), tamanho_chunk)] or [b""]
    total = len(blocos)

    for indice, bloco in enumerate(blocos):
        await servico.send(
            destino,
            {
                "comando": COMANDO_CHUNK,
                "nome_arquivo": caminho_backup.name,
                "indice": indice,
                "total": total,
                "dados_base64": base64.b64encode(bloco).decode("ascii"),
            },
        )
    logger.info("Replica enviada: %s (%d blocos)", caminho_backup.name, total)
    return total


class ReceptorReplica:
    """Lado que recebe os blocos e reconstroi o arquivo.

    Monta por indice (nao pela ordem de chegada): o comm.send garante
    entrega de cada bloco, mas retransmissoes nao garantem que cheguem em
    sequencia.
    """

    def __init__(self, diretorio_destino: Path | str) -> None:
        self._diretorio_destino = Path(diretorio_destino)
        self._diretorio_destino.mkdir(parents=True, exist_ok=True)
        self._blocos_em_andamento: dict[str, dict[int, bytes]] = {}

    def registrar(self, event_bus: EventBus) -> None:
        event_bus.subscribe("comm.mensagem.command", self._ao_receber_comando)

    async def _ao_receber_comando(self, evento: Evento) -> None:
        payload = evento.dados.get("payload", {})
        if payload.get("comando") != COMANDO_CHUNK:
            return

        nome_arquivo = payload["nome_arquivo"]
        indice = payload["indice"]
        total = payload["total"]
        bloco = base64.b64decode(payload["dados_base64"])

        pendentes = self._blocos_em_andamento.setdefault(nome_arquivo, {})
        pendentes[indice] = bloco

        if len(pendentes) == total:
            self._finalizar_arquivo(nome_arquivo)

    def _finalizar_arquivo(self, nome_arquivo: str) -> None:
        blocos = self._blocos_em_andamento.pop(nome_arquivo)
        destino = self._diretorio_destino / nome_arquivo
        with open(destino, "wb") as arquivo:
            for indice in sorted(blocos):
                arquivo.write(blocos[indice])
        logger.info("Replica reconstruida: %s", destino)

    def arquivo_completo(self, nome_arquivo: str) -> Path | None:
        caminho = self._diretorio_destino / nome_arquivo
        return caminho if caminho.exists() else None
