"""Cadastra uma pessoa conhecida (dono/morador) no Fofao (Caps 8, 11).

Roda no NOTEBOOK: abre a camera, captura alguns frames, extrai o embedding
do rosto (face_recognition) e grava a pessoa na tabela `pessoas` do banco
no SSD do Raspberry, via TCP (memory.remember). Depois disso o robo passa a
reconhecer essa pessoa como conhecida - todo rosto que NAO casar com um
cadastro e um "estranho" (base do Modo Sentinela).

O embedding e binario (128 floats), entao vai embrulhado em base64 no campo
embedding_face; a Ponte de Memoria no Raspberry desembrulha para BLOB.

Uso (no Notebook, com a pessoa em frente a camera):
    .venv/bin/python tools/cadastrar_dono.py "Joao" --autorizacao dono
    .venv/bin/python tools/cadastrar_dono.py "Maria" --camera 0 --fotos 7

Dicas: boa luz no rosto, olhando para a camera; capturamos varios frames e
tiramos a media dos embeddings para um cadastro mais estavel. Se ninguem
for reconhecido depois, rode de novo com mais --fotos ou melhor iluminacao.
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

from orion.communication.service import ComunicacaoService  # noqa: E402
from orion.communication.transport import TcpTransport  # noqa: E402
from orion.kernel.config import ConfigurationManager  # noqa: E402
from orion.kernel.event_bus import EventBus  # noqa: E402
from orion.vision.captura import CapturaCamera  # noqa: E402
from orion.vision.reconhecimento_facial import ReconhecedorFacial  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("cadastrar_dono")

try:
    import cv2
except ImportError:  # pragma: no cover - so no Notebook com a visao instalada
    cv2 = None


async def _capturar_embeddings(indice_camera: int, espelhado: bool, n_fotos: int) -> list[np.ndarray]:
    """Captura ate n_fotos frames e devolve os embeddings dos que tinham um
    rosto claro. face_recognition espera RGB; a camera entrega BGR."""
    camera = CapturaCamera(indice=indice_camera, espelhado=espelhado)
    await camera.abrir()
    embeddings: list[np.ndarray] = []
    try:
        tentativas = 0
        while len(embeddings) < n_fotos and tentativas < n_fotos * 3:
            tentativas += 1
            frame_bgr = await camera.ler_frame()
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB) if cv2 else frame_bgr
            emb = ReconhecedorFacial.gerar_embedding(frame_rgb)
            if emb is not None:
                embeddings.append(emb)
                logger.info("rosto capturado (%d/%d)", len(embeddings), n_fotos)
            else:
                logger.info("nenhum rosto neste frame - ajuste a posicao/luz")
            await asyncio.sleep(0.3)
    finally:
        await camera.fechar()
    return embeddings


async def principal() -> int:
    parser = argparse.ArgumentParser(description="Cadastra um dono/morador conhecido do Fofao.")
    parser.add_argument("nome", help="Nome da pessoa (ex.: Joao)")
    parser.add_argument(
        "--autorizacao", default="dono", help="Nivel: dono | morador | visitante (padrao: dono)"
    )
    parser.add_argument("--camera", type=int, default=None, help="Indice da camera (padrao: config)")
    parser.add_argument("--fotos", type=int, default=5, help="Quantos frames com rosto capturar")
    args = parser.parse_args()

    config = ConfigurationManager("config/orion.yaml").carregar()
    secao_visao = config.secao("vision")
    conf_raspberry = config.secao("communication")["raspberry"]
    indice_camera = args.camera if args.camera is not None else secao_visao["camera_indice_principal"]

    logger.info("Abrindo camera %d - olhe para a camera com boa luz...", indice_camera)
    embeddings = await _capturar_embeddings(
        indice_camera, secao_visao["camera_frontal_espelhada"], args.fotos
    )
    if not embeddings:
        logger.error(
            "Nenhum rosto capturado. Verifique a camera, a iluminacao e se ha um rosto no quadro."
        )
        return 1

    # Media dos embeddings = cadastro mais estavel (menos sensivel a um frame
    # ruim). O tipo float64 e o que o reconhecimento espera ao ler o BLOB.
    embedding_medio = np.mean(embeddings, axis=0).astype(np.float64)
    embedding_b64 = base64.b64encode(embedding_medio.tobytes()).decode("ascii")

    bus = EventBus()
    comm = ComunicacaoService("mission_core", bus)
    transporte = TcpTransport(conf_raspberry["host"], conf_raspberry["tcp_port"])
    logger.info("Conectando no Motion Core %s:%d...", conf_raspberry["host"], conf_raspberry["tcp_port"])
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
        pessoa_id = resposta.payload.get("resultado")
        logger.info(
            "CADASTRADO! %s (%s) e a pessoa id=%s. O Fofao agora reconhece esse rosto.",
            args.nome,
            args.autorizacao,
            pessoa_id,
        )
        return 0
    logger.error("Falha ao cadastrar: %s", resposta.payload.get("erro"))
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
