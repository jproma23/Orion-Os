"""Cliente de memoria do Mission Core (Cap 7 secao 2, 5; Cap 11 secao 6).

O banco/memoria vivem no Raspberry (Fase 3) - o Notebook so acessa via
comm.request (Cap 14), nunca direto. Este cliente espelha a interface da
`MemoryAPI` (motion_core/memory/api.py) mas cada metodo vira uma mensagem
COMMAND/RESPONSE para "motion_core", tratada la por `PonteMemoria`.
"""
from __future__ import annotations

from orion.communication.service import ComunicacaoService


class ErroMemoriaRemota(Exception):
    """A resposta do Raspberry veio com ok=False (erro reportado pela PonteMemoria)."""


class MemoryClient:
    def __init__(
        self, servico: ComunicacaoService, destino: str = "motion_core", timeout_s: float = 2.0
    ) -> None:
        self._servico = servico
        self._destino = destino
        self._timeout_s = timeout_s

    async def _chamar(self, operacao: str, **payload) -> object:
        resposta = await self._servico.request(
            self._destino, {"comando": f"memory.{operacao}", **payload}, timeout_s=self._timeout_s
        )
        if not resposta.payload.get("ok", False):
            raise ErroMemoriaRemota(resposta.payload.get("erro", "erro desconhecido"))
        return resposta.payload.get("resultado")

    async def remember(self, categoria: str, dados: dict) -> int:
        return await self._chamar("remember", categoria=categoria, dados=dados)

    async def recall(self, categoria: str, filtro: dict | None = None, limite: int = 20) -> list[dict]:
        return await self._chamar("recall", categoria=categoria, filtro=filtro, limite=limite)

    async def update(self, categoria: str, id: int, dados: dict) -> bool:
        return await self._chamar("update", categoria=categoria, id=id, dados=dados)

    async def forget(self, categoria: str, id: int) -> bool:
        return await self._chamar("forget", categoria=categoria, id=id)

    async def context(self, pessoa_id: int | None = None, limite_conversas: int = 10) -> dict:
        return await self._chamar(
            "context", pessoa_id=pessoa_id, limite_conversas=limite_conversas
        )

    async def stats(self) -> dict:
        return await self._chamar("stats")
