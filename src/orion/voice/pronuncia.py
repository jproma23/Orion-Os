"""Acento de volta antes da voz: "relacao" vira "relação" (2026-11-14).

Dono: *"em vez dele falar cao queria que ele falasse ção"* / *"relação, etc"*.
O Piper le o que esta escrito, e boa parte do que o robo fala esta escrita
SEM ACENTO: a memoria de si (content/saber, "Relacao dono e Atom... nao um
usuario"), as falas prontas do codigo ("Nao consigo enxergar agora") e a
visao. "relacao" saia "relacão"; "nao", "nau".

Corrige no UNICO ponto por onde todo texto passa antes de virar som
(`Sintetizador._sintetizar`) - e nao em cada fonte, que sao dezenas e
voltariam a nascer sem acento.

So o que e INEQUIVOCO em portugues escrito sem acento:
- "-cao"/"-coes" -> "-ção"/"-ções" (relação, configuração, ações);
- "-ao"/"-aos"/"-oes" -> "-ão"/"-ãos"/"-ões" (não, visão, João, mãos, botões),
  menos a preposicao "ao"/"aos" e "caos";
- um punhado de palavras que sem acento so tem uma leitura (você, também,
  até, já, robô...).
Nada de "e"/"é" ou "esta"/"está": ali a grafia sem acento e AMBIGUA, e
chutar errado e pior que deixar como esta. Texto que ja vem acentuado (a IA
escreve com acento) nao tem o que casar e passa igual.
"""
from __future__ import annotations

import re

#: Palavras que, sem acento, so tem uma leitura possivel.
_PALAVRAS = {
    "voce": "você", "voces": "vocês", "tambem": "também", "ate": "até",
    "ja": "já", "apos": "após", "porem": "porém", "alem": "além",
    "atras": "atrás", "robo": "robô", "robos": "robôs", "proximo": "próximo",
    "proxima": "próxima", "numero": "número", "unico": "único",
    "unica": "única", "rapido": "rápido", "rapida": "rápida",
    "possivel": "possível", "impossivel": "impossível", "camera": "câmera",
    "cameras": "câmeras", "rele": "relé", "saude": "saúde", "familia": "família",
    "historia": "história", "memoria": "memória", "bussola": "bússola",
    "voo": "voo", "dificil": "difícil", "facil": "fácil", "util": "útil",
    "otimo": "ótimo", "otima": "ótima", "pratica": "prática", "logica": "lógica",
    "musica": "música", "fisica": "física", "quimica": "química",
    "matematica": "matemática", "eletrica": "elétrica", "eletrico": "elétrico",
    "graus": "graus", "aula": "aula",
}
_NAO_MEXER = {"ao", "aos", "caos", "cao"}  # "cao" sozinho vira "cão" abaixo

_PALAVRA = re.compile(r"[A-Za-z]+")


def _com_caixa(original: str, nova: str) -> str:
    """Mantem MAIUSCULAS e Inicial Maiuscula do original."""
    if original.isupper() and len(original) > 1:
        return nova.upper()
    if original[:1].isupper():
        return nova[:1].upper() + nova[1:]
    return nova


def _corrigir(palavra: str) -> str:
    baixa = palavra.lower()
    if baixa in _PALAVRAS:
        return _com_caixa(palavra, _PALAVRAS[baixa])
    if baixa == "cao":
        return _com_caixa(palavra, "cão")
    if baixa in _NAO_MEXER or len(baixa) < 3:
        return palavra
    for fim, troca in (("coes", "ções"), ("cao", "ção"), ("oes", "ões"),
                       ("aos", "ãos"), ("ao", "ão")):
        if baixa.endswith(fim):
            return _com_caixa(palavra, baixa[: -len(fim)] + troca)
    return palavra


def acentuar(texto: str) -> str:
    """Devolve o texto com o acento que falta onde ele e inequivoco."""
    return _PALAVRA.sub(lambda m: _corrigir(m.group(0)), texto)
