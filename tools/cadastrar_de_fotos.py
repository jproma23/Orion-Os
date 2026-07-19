"""Cadastra uma pessoa conhecida a partir de FOTOS (arquivos), não da câmera.

Ideal para cadastrar a família com fotos baixadas do Google Fotos: aponte
para uma pasta com várias fotos da pessoa, o script extrai o embedding do
rosto de cada foto, tira a média (cadastro mais estável) e grava na tabela
`pessoas` do banco no SSD do Raspberry, via TCP.

Quanto mais fotos boas (rosto claro, de frente, luz variada), melhor o
reconhecimento. Fotos sem rosto detectável são ignoradas (com aviso).

Uso (no Notebook):
    .venv/bin/python tools/cadastrar_de_fotos.py "João Paulo" data/fotos/joaopaulo --autorizacao dono
    .venv/bin/python tools/cadastrar_de_fotos.py "Bruno" data/fotos/bruno --autorizacao morador
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import face_recognition  # noqa: E402

from orion.communication.service import ComunicacaoService  # noqa: E402
from orion.communication.transport import TcpTransport  # noqa: E402
from orion.kernel.config import ConfigurationManager  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("cadastrar_de_fotos")

EXTENSOES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _embeddings_da_pasta(pasta: Path) -> list[np.ndarray]:
    """Extrai o embedding do primeiro rosto de cada imagem da pasta."""
    embeddings: list[np.ndarray] = []
    imagens = sorted(p for p in pasta.iterdir() if p.suffix.lower() in EXTENSOES)
    if not imagens:
        logger.error("Nenhuma imagem em %s (extensoes: %s)", pasta, ", ".join(sorted(EXTENSOES)))
        return embeddings
    for caminho in imagens:
        try:
            imagem = face_recognition.load_image_file(str(caminho))  # ja vem em RGB
            locais = face_recognition.face_locations(imagem)
            if not locais:
                logger.warning("sem rosto em %s - ignorada", caminho.name)
                continue
            cods = face_recognition.face_encodings(imagem, locais)
            if cods:
                embeddings.append(cods[0])
                logger.info("rosto ok: %s", caminho.name)
        except Exception as erro:
            logger.warning("falha ao ler %s: %s", caminho.name, erro)
    return embeddings


async def principal() -> int:
    parser = argparse.ArgumentParser(description="Cadastra uma pessoa a partir de fotos.")
    parser.add_argument("nome", help="Nome da pessoa (ex.: 'João Paulo')")
    parser.add_argument("pasta", help="Pasta com as fotos da pessoa")
    parser.add_argument(
        "--autorizacao", default="morador", help="dono | morador | visitante (padrao: morador)"
    )
    args = parser.parse_args()

    pasta = Path(args.pasta)
    if not pasta.is_dir():
        logger.error("Pasta nao encontrada: %s", pasta)
        return 1

    logger.info("Lendo fotos de %s ...", pasta)
    embeddings = _embeddings_da_pasta(pasta)
    if not embeddings:
        logger.error("Nenhum rosto utilizavel. Use fotos com o rosto claro e de frente.")
        return 1
    logger.info("%d rosto(s) aproveitado(s) - calculando a media", len(embeddings))

    embedding_medio = np.mean(embeddings, axis=0).astype(np.float64)
    embedding_b64 = base64.b64encode(embedding_medio.tobytes()).decode("ascii")

    config = ConfigurationManager("config/orion.yaml").carregar()
    conf_raspberry = config.secao("communication")["raspberry"]
    comm = ComunicacaoService("mission_core", EventBus())
    transporte = TcpTransport(conf_raspberry["host"], conf_raspberry["tcp_port"])
    logger.info("Conectando no Motion Core %s:%d ...", conf_raspberry["host"], conf_raspberry["tcp_port"])
    await transporte.conectar()
    comm.adicionar_link("motion_core", transporte)

    try:
        resposta = await comm.request(
            "motion_core",
            {
                "comando": "memory.remember",
                "categoria": "pessoas",
                "dados": {
                    "nome": args.nome,
                    "autorizacao": args.autorizacao,
                    "embedding_face": {"_bytes_b64": embedding_b64},
                },
            },
            timeout_s=5.0,
        )
    finally:
        await comm.encerrar()

    if resposta.payload.get("ok"):
        logger.info(
            "CADASTRADO! %s (%s), id=%s, a partir de %d foto(s). O Fofao ja reconhece.",
            args.nome,
            args.autorizacao,
            resposta.payload.get("resultado"),
            len(embeddings),
        )
        return 0
    logger.error("Falha ao cadastrar: %s", resposta.payload.get("erro"))
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
