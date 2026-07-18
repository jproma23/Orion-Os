"""Reconhecimento facial de pessoas autorizadas (Cap 8 secao 4; Cap 11).

Os embeddings de rosto vem da tabela `pessoas.embedding_face` (Cap 11 s.5),
lida via `memory.recall` e carregada aqui com `carregar_pessoas_conhecidas`.
Comparacao por distancia euclidiana entre embeddings - o mesmo metodo que a
biblioteca `face_recognition` recomenda para seus proprios encodings.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import face_recognition
import numpy as np

LIMIAR_DISTANCIA_PADRAO = 0.6


@dataclass
class RostoReconhecido:
    pessoa_id: int | None
    nome: str | None
    confianca: float
    caixa: tuple[int, int, int, int]  # left, top, right, bottom


class ReconhecedorFacial:
    def __init__(self, limiar_distancia: float = LIMIAR_DISTANCIA_PADRAO) -> None:
        self._limiar = limiar_distancia
        self._pessoas_conhecidas: list[tuple[int, str, np.ndarray]] = []

    def carregar_pessoas_conhecidas(self, pessoas: list[dict]) -> None:
        """`pessoas`: lista de dicts com id, nome, embedding_face (bytes,
        vindos direto de `memory.recall("pessoas", ...)`)."""
        conhecidas = []
        for pessoa in pessoas:
            embedding_bytes = pessoa.get("embedding_face")
            if not embedding_bytes:
                continue
            embedding = np.frombuffer(embedding_bytes, dtype=np.float64)
            conhecidas.append((pessoa["id"], pessoa["nome"], embedding))
        self._pessoas_conhecidas = conhecidas

    async def reconhecer(self, frame_rgb: np.ndarray) -> list[RostoReconhecido]:
        def _processar() -> list[RostoReconhecido]:
            localizacoes = face_recognition.face_locations(frame_rgb)
            embeddings = face_recognition.face_encodings(frame_rgb, localizacoes)
            resultados = []
            for (topo, direita, base, esquerda), embedding in zip(localizacoes, embeddings):
                pessoa_id, nome, confianca = self._melhor_correspondencia(embedding)
                resultados.append(
                    RostoReconhecido(
                        pessoa_id=pessoa_id,
                        nome=nome,
                        confianca=confianca,
                        caixa=(esquerda, topo, direita, base),
                    )
                )
            return resultados

        return await asyncio.to_thread(_processar)

    def _melhor_correspondencia(
        self, embedding: np.ndarray
    ) -> tuple[int | None, str | None, float]:
        if not self._pessoas_conhecidas:
            return None, None, 0.0

        distancias = [
            float(np.linalg.norm(embedding - conhecida[2])) for conhecida in self._pessoas_conhecidas
        ]
        indice_menor = int(np.argmin(distancias))
        menor_distancia = distancias[indice_menor]
        if menor_distancia > self._limiar:
            return None, None, 0.0

        pessoa_id, nome, _ = self._pessoas_conhecidas[indice_menor]
        confianca = max(0.0, 1.0 - menor_distancia)
        return pessoa_id, nome, confianca

    @staticmethod
    def gerar_embedding(frame_rgb: np.ndarray) -> np.ndarray | None:
        """Usado para CADASTRAR uma pessoa nova: extrai o embedding do
        primeiro rosto encontrado no frame (Cap 11 s.7: aprendizado
        continuo, mas a decisao de gravar fica com quem chama)."""
        localizacoes = face_recognition.face_locations(frame_rgb)
        if not localizacoes:
            return None
        embeddings = face_recognition.face_encodings(frame_rgb, localizacoes)
        return embeddings[0] if embeddings else None
