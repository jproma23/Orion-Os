"""Cliente de memoria do Mission Core (Cap 7 secao 2, 5; Cap 11 secao 6).

O banco/memoria vivem no Raspberry (Fase 3) - o Notebook so acessa via
comm.request (Cap 14), nunca direto. Este cliente espelha a interface da
`MemoryAPI` (motion_core/memory/api.py) mas cada metodo vira uma mensagem
COMMAND/RESPONSE para "motion_core", tratada la por `PonteMemoria`.
"""
from __future__ import annotations

import base64
from typing import Any

from orion.communication.service import ComunicacaoService

#: mesmo marcador de campo binario da PonteMemoria (bridge.py): campos BLOB
#: (embedding_face) viajam como {"_bytes_b64": "<base64>"} no JSON e sao
#: desembrulhados de volta para bytes aqui.
CHAVE_BINARIO = "_bytes_b64"


def _decodificar_binarios(valor: Any) -> Any:
    """Converte recursivamente {"_bytes_b64": str} de volta em bytes."""
    if isinstance(valor, dict):
        if list(valor.keys()) == [CHAVE_BINARIO] and isinstance(valor[CHAVE_BINARIO], str):
            return base64.b64decode(valor[CHAVE_BINARIO])
        return {chave: _decodificar_binarios(v) for chave, v in valor.items()}
    if isinstance(valor, list):
        return [_decodificar_binarios(v) for v in valor]
    return valor


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
        return _decodificar_binarios(resposta.payload.get("resultado"))

    async def remember(self, categoria: str, dados: dict) -> int:
        return await self._chamar("remember", categoria=categoria, dados=dados)

    async def recall(self, categoria: str, filtro: dict | None = None, limite: int = 20) -> list[dict]:
        return await self._chamar("recall", categoria=categoria, filtro=filtro, limite=limite)

    async def update(self, categoria: str, id: int, dados: dict) -> bool:
        return await self._chamar("update", categoria=categoria, id=id, dados=dados)

    async def forget(self, categoria: str, id: int) -> bool:
        return await self._chamar("forget", categoria=categoria, id=id)

    async def context(
        self,
        pessoa_id: int | None = None,
        limite_conversas: int = 10,
        sobre: str | None = None,
    ) -> dict:
        """`sobre` e a fala atual do usuario. Vai junto para o banco poder
        devolver conhecimento RELEVANTE aquilo, e nao apenas o mais
        recente - ver `MemoryAPI._conhecimento_relevante`."""
        return await self._chamar(
            "context", pessoa_id=pessoa_id, limite_conversas=limite_conversas, sobre=sobre
        )

    async def stats(self) -> dict:
        return await self._chamar("stats")

    async def nota_escrever(self, titulo: str, conteudo: str, links: list[str] | None = None) -> str:
        return await self._chamar("nota_escrever", titulo=titulo, conteudo=conteudo, links=links)

    async def projeto_buscar(
        self, consulta: str, limite: int = 5, tipo: str | None = None
    ) -> list[dict]:
        """A memoria do PROJETO sobre si mesmo - journal, commits, EDRs e o
        proprio codigo, com a referencia (hash ou arquivo:linha) junto.

        A referencia e o que faz isto valer: sem ela o modelo repete o
        trecho com a mesma cara de certeza que teria inventando.
        """
        return await self._chamar(
            "projeto_buscar", consulta=consulta, limite=limite, tipo=tipo
        )

    async def nota_buscar(self, consulta: str, limite: int = 5) -> list[dict]:
        return await self._chamar("nota_buscar", consulta=consulta, limite=limite)

    async def saber_guardar(self, categoria: str, dados: dict) -> int:
        """Grava na memoria cognitiva do ORION - o acervo do "Eu ja sei?".

        Categorias: conhecimentos, experiencias, procedimentos, dispositivos,
        relacoes. Cada gravacao indexa o texto no FTS para o portao achar.
        """
        return await self._chamar("saber_guardar", categoria=categoria, dados=dados)

    async def saber_buscar(self, consulta: str, limite: int = 3) -> list[dict]:
        """O que o ORION ja sabe que COBRE a fala - o portao do "Eu ja sei?".

        Vazio = ele nao sabe, e a cascata segue para a IA (a consultora
        externa). Cada achado traz categoria, titulo, corpo e nota_bm25.
        """
        return await self._chamar("saber_buscar", consulta=consulta, limite=limite)

    async def saber_stats(self) -> dict:
        """Contagens da memoria cognitiva + quantas vezes a IA foi consultada."""
        return await self._chamar("saber_stats")

    async def registrar_consulta_ia(self) -> None:
        """Conta mais uma consulta a IA - a medida de dependencia externa.

        Chamado quando a conversa efetivamente caiu no modelo.
        """
        return await self._chamar("registrar_consulta_ia")
