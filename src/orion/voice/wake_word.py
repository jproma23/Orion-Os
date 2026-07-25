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

Tolerancia a erro de transcricao (2026-07-24, achado real testado ao vivo):
o Whisper as vezes acerta quase tudo, mas erra de um jeito especifico -
catalogado ao vivo numa sessao de calibracao com bastante ruido de fundo:
  - 'FO FÃO' (com espaco no meio) - a palavra saiu certa, so quebrada em
    dois tokens. Isso e resolvido juntando pares de tokens vizinhos.
  - 'Fulfo', 'FAMO', 'Ei, oui!' - transcricoes bem mais distantes
    foneticamente. Testado (distancia de Levenshtein 'fulfo'->'fofao' = 3):
    NAO da pra pegar isso com uma tolerancia pequena sem arriscar falso
    positivo em qualquer palavra curta do dia a dia - fica de fora de
    proposito. O usuario so precisa repetir "Fofao" nesses casos (o que
    ja acontecia na pratica: o robo respondeu certo depois de poucas
    tentativas na mesma sessao de teste).
A tolerancia por distancia de edicao (passo 3 de `verificar`) cobre so
erro pequeno de UMA letra (troca/falta/sobra), o suficiente pra pegar
transcricoes quase certas sem abrir mao de exigir a palavra de verdade.
"""
from __future__ import annotations

import re

PALAVRAS_ATIVACAO_PADRAO = ("fofao", "fofão")
#: edicoes (substituicao/insercao/remocao de 1 letra) toleradas por
#: palavra na checagem foneticamente tolerante (passo 3) - pequeno de
#: proposito, ver docstring do modulo.
DISTANCIA_MAXIMA_TOLERADA = 1


def _normalizar(texto: str) -> str:
    texto = texto.lower().strip()
    for acentuado, simples in (("ã", "a"), ("á", "a"), ("â", "a"), ("à", "a")):
        texto = texto.replace(acentuado, simples)
    return texto


def _distancia_levenshtein(a: str, b: str) -> int:
    """Distancia de edicao classica (Wagner-Fischer). Sem biblioteca
    externa - as strings aqui sao sempre curtas (~5 letras, uma palavra),
    custo computacional desprezivel."""
    if a == b:
        return 0
    linha_anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        linha_atual = [i]
        for j, cb in enumerate(b, start=1):
            custo = 0 if ca == cb else 1
            linha_atual.append(
                min(
                    linha_anterior[j] + 1,  # remocao
                    linha_atual[j - 1] + 1,  # insercao
                    linha_anterior[j - 1] + custo,  # substituicao
                )
            )
        linha_anterior = linha_atual
    return linha_anterior[-1]


class DetectorPalavraAtivacao:
    def __init__(
        self,
        palavras_ativacao: tuple[str, ...] = PALAVRAS_ATIVACAO_PADRAO,
        distancia_maxima: int = DISTANCIA_MAXIMA_TOLERADA,
    ) -> None:
        self._palavras = tuple(_normalizar(p) for p in palavras_ativacao)
        self._distancia_maxima = distancia_maxima

    def verificar(self, texto_transcrito: str) -> bool:
        texto_normalizado = _normalizar(texto_transcrito)

        # 1) Casamento exato com borda de palavra - caminho original, mais
        #    rapido e mais confiavel quando o Whisper acerta em cheio.
        if any(re.search(rf"\b{re.escape(p)}\b", texto_normalizado) for p in self._palavras):
            return True

        tokens = re.findall(r"[a-z]+", texto_normalizado)

        # 2) O Whisper as vezes quebra "fofao" em dois tokens ("fo fao") -
        #    junta cada par de tokens vizinhos e tenta de novo. Achado
        #    real: 'FO FÃO' nao batia so por causa do espaco no meio.
        for i in range(len(tokens) - 1):
            if tokens[i] + tokens[i + 1] in self._palavras:
                return True

        # 3) Tolerancia a pequeno erro fonetico (distancia de edicao) - so
        #    em tokens de tamanho parecido com "fofao"/"fofão" (5 letras),
        #    pra nao disparar em qualquer palavra curta do dia a dia.
        for token in tokens:
            if abs(len(token) - 5) > 2:
                continue
            if any(
                _distancia_levenshtein(token, p) <= self._distancia_maxima
                for p in self._palavras
            ):
                return True

        return False
