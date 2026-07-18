"""Deteccao da palavra de ativacao "Fofao" (Cap 9 secao 3-4).

Ainda nao existe um modelo customizado treinado para "Fofao" no
openWakeWord (os modelos prontos da lib sao em ingles - "hey jarvis",
"alexa" etc.; treinar um customizado exige um pipeline de dados sinteticos
separado, fora do escopo desta fase - `openwakeword` fica instalado como
dependencia para quando esse modelo existir).

Solucao que funciona hoje, 100% offline: transcrever janelas curtas de
audio com faster-whisper (mesmo motor da Fase 6 usado para o comando
completo) e checar se "fofao" aparece no texto. Mais lento que um modelo
de wake-word dedicado (nao roda em segundo plano com CPU minima), mas
funcional sem precisar treinar nada agora. Quando o modelo customizado
existir, trocar `DetectorPalavraAtivacao` por um wrapper de
`openwakeword.Model(wakeword_model_paths=[...])`, mantendo a mesma
interface (`verificar`).
"""
from __future__ import annotations

import re

PALAVRAS_ATIVACAO_PADRAO = ("fofao", "fofão")


def _normalizar(texto: str) -> str:
    texto = texto.lower().strip()
    for acentuado, simples in (("ã", "a"), ("á", "a"), ("â", "a"), ("à", "a")):
        texto = texto.replace(acentuado, simples)
    return texto


class DetectorPalavraAtivacao:
    def __init__(self, palavras_ativacao: tuple[str, ...] = PALAVRAS_ATIVACAO_PADRAO) -> None:
        self._palavras = tuple(_normalizar(p) for p in palavras_ativacao)

    def verificar(self, texto_transcrito: str) -> bool:
        texto_normalizado = _normalizar(texto_transcrito)
        return any(re.search(rf"\b{re.escape(p)}\b", texto_normalizado) for p in self._palavras)
