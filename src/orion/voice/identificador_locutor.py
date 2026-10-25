"""Identificacao de LOCUTOR por voz - quem esta falando, so pela voz.

Espelha o reconhecimento facial (reconhecimento_facial.py): o embedding de
voz de cada pessoa cadastrada vem da tabela `pessoas.embedding_voz` (lida
via memory.recall) e a fala atual e comparada por Cosseno - o metodo que o
SpeechBrain ECAPA recomenda para os proprios embeddings (por isso
diferente da distancia euclidiana do rosto: cada modelo pede a metrica
dele).

## O buraco que isto fecha

O robo conversa por voz sem reconhecer o rosto de quem fala (docs/journal
e memoria: "conversa por voz sem reconhecer rosto" - as conversas voltam a
`pessoa_id = NULL`). O `quem_fala` so sabia quem estava na frente da
camera. Este modulo da a MESMA atribuicao pela voz: quem fala no
microfone, mesmo fora do quadro da camera, passa a ter a conversa
creditada.

## Atribuir errado e PIOR que nao atribuir (mesma regra do rosto)

Sem voiceprint cadastrada, ou com a melhor correspondencia abaixo do
limiar, `identificar` devolve None e a conversa fica "da casa" - nunca se
inventa um nome. O limiar de cosseno (0.5) e o freio: baixar deixa o robo
confundir vozes da familia, subir deixa ele nao reconhecer ninguem.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

logger = logging.getLogger("orion.voice.identificador_locutor")

#: Cosseno minimo para dizer "e esta pessoa". ECAPA normalizado produz
#: cossenos ~0.6-0.9 para a mesma voz e ~0.2-0.5 para vozes diferentes;
#: 0.5 e um ponto conservador (erro para NAO atribuir).
LIMIAR_COSSENO_PADRAO = 0.5
FONTE_MODELO_PADRAO = "speechbrain/spkrec-ecapa-voxceleb"


@dataclass
class LocutorIdentificado:
    pessoa_id: int | None
    nome: str | None
    confianca: float


def _carregar_classificador(
    fonte: str = FONTE_MODELO_PADRAO, savedir: str | None = None
) -> Any:
    """Carrega (e baixa na primeira vez) o EncoderClassifier ECAPA.

    O download vive em `.cache_speechbrain` (savedir) para nao repetir a
    cada carga. Import tardio: speechbrain puxa torchaudio e amigos, e so
    quem usa identificacao de voz precisa pagar essa conta.
    """
    from speechbrain.inference.speaker import EncoderClassifier

    kwargs: dict[str, Any] = {}
    if savedir:
        kwargs["savedir"] = savedir
    return EncoderClassifier.from_hparams(source=fonte, **kwargs)


def gerar_embedding(
    audio,
    fonte: str = FONTE_MODELO_PADRAO,
    savedir: str | None = None,
) -> np.ndarray:
    """Embedding de voz de um trecho (float32, mono, 16 kHz) - usado no
    cadastro/enrolment da familia. Normalizado (L2): o cosseno vira dot."""
    import torch

    classificador = _carregar_classificador(fonte, savedir)
    wav = torch.from_numpy(np.asarray(audio, dtype=np.float32))
    if wav.ndim == 1:
        wav = wav.unsqueeze(0)
    with torch.no_grad():
        emb = classificador.encode_batch(wav, wav_lens=torch.ones(wav.shape[0]))
    vetor = emb.squeeze(0).squeeze(0).numpy().astype(np.float32)
    norma = float(np.linalg.norm(vetor))
    return vetor / (norma + 1e-9)


class IdentificadorLocutor:
    """Compara a fala atual com os voiceprints da familia e devolve quem e.

    O modelo ECAPA carrega SOB DEMANDA (na primeira `identificar`), para o
    boot nao pagar ~26 s de carga quando nem ha voiceprint cadastrado.
    `carregar_modelo` e injetavel para os testes trocarem o ECAPA por um
    gerador de vetores deterministico.
    """

    def __init__(
        self,
        limiar_cosseno: float = LIMIAR_COSSENO_PADRAO,
        fonte_modelo: str = FONTE_MODELO_PADRAO,
        savedir: str | None = None,
        carregar_modelo: Callable[[], Any] | None = None,
    ) -> None:
        self._limiar = limiar_cosseno
        self._fonte = fonte_modelo
        self._savedir = savedir
        self._carregar_modelo = carregar_modelo
        self._classificador: Any | None = None
        #: id, nome, embedding(voz) NORMALIZADO - mesmo formato do rosto.
        self._vozes: list[tuple[int, str, np.ndarray]] = []

    def carregar_vozes_conhecidas(self, pessoas: list[dict]) -> None:
        """`pessoas`: lista de dicts com id, nome, embedding_voz (bytes,
        vindos direto de `memory.recall("pessoas", ...)`)."""
        conhecidas = []
        for pessoa in pessoas:
            bloco = pessoa.get("embedding_voz")
            if not bloco:
                continue
            embedding = np.frombuffer(bloco, dtype=np.float32)
            norma = float(np.linalg.norm(embedding))
            if norma == 0.0:
                continue
            conhecidas.append((pessoa["id"], pessoa["nome"], embedding / norma))
        self._vozes = conhecidas
        logger.info(
            "carregada(s) %d voz(es) de familia para identificacao de locutor",
            len(conhecidas),
        )

    def _classificador_para(self) -> Any:
        if self._classificador is None:
            if self._carregar_modelo is not None:
                self._classificador = self._carregar_modelo()
            else:
                self._classificador = _carregar_classificador(self._fonte, self._savedir)
        return self._classificador

    def _embedding_de(self, audio) -> np.ndarray:
        import torch

        wav = torch.from_numpy(np.asarray(audio, dtype=np.float32))
        if wav.ndim == 1:
            wav = wav.unsqueeze(0)
        classificador = self._classificador_para()
        with torch.no_grad():
            emb = classificador.encode_batch(wav, wav_lens=torch.ones(wav.shape[0]))
        vetor = emb.squeeze(0).squeeze(0).numpy().astype(np.float32)
        norma = float(np.linalg.norm(vetor))
        return vetor / (norma + 1e-9)

    def _identificar_sincrono(self, audio) -> LocutorIdentificado | None:
        if not self._vozes:
            # Ninguem tem voiceprint: nao ha o que comparar. A conversa
            # volta a ser "da casa", que e o comportamento de sempre.
            return None
        vetor = self._embedding_de(audio)
        melhor_id, melhor_nome, melhor_cosseno = None, None, 0.0
        for pessoa_id, nome, emb in self._vozes:
            cosseno = float(np.dot(vetor, emb))
            if cosseno > melhor_cosseno:
                melhor_cosseno, melhor_id, melhor_nome = cosseno, pessoa_id, nome
        if melhor_cosseno < self._limiar:
            logger.info(
                "locutor nao identificado por voz (melhor cosseno=%.3f < limiar %.3f)",
                melhor_cosseno,
                self._limiar,
            )
            return None
        logger.info(
            "locutor identificado por voz: '%s' (cosseno=%.3f)", melhor_nome, melhor_cosseno
        )
        return LocutorIdentificado(
            pessoa_id=melhor_id, nome=melhor_nome, confianca=melhor_cosseno
        )

    async def identificar(self, audio) -> LocutorIdentificado | None:
        """Quem falou, ou None se ninguem passou no limiar. O ECAPA e
        bloqueante - roda em thread como o Whisper e o YOLO."""
        return await asyncio.to_thread(self._identificar_sincrono, audio)