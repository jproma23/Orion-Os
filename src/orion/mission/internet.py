"""Consulta a internet quando o robo nao sabe - dizendo de onde tirou.

Pedido do dono em 2026-08-07: *"poem o robo para acessar a internet
consultar em busca de resposta"*.

## A REGRA QUE GOVERNA ESTE MODULO

Tudo que sai daqui **tem procedencia colada**: titulo, trecho e URL. Nao
existe caminho por onde um texto da internet chegue ao robo sem dizer que
veio de fora.

Isso nao e formalidade. O projeto inteiro gira em torno de uma distincao:
o que o robo MEDIU nesta casa e verdade dele; o resto, nao. Ele ja
respondeu uma hora plausivel para uma pergunta que ninguem fez ("que
horas"), ja contou que foi a cozinha sem sair do lugar, e ja inventou 27
falas que ninguem disse. Texto de fora, sem etiqueta, seria a quarta forma
do mesmo defeito - e a mais convincente, porque vem redigido.

## POR QUE WIKIPEDIA, E POR QUE SEM CHAVE

Nao exige cadastro, responde em ~0,3 s (medido do Pi em 07/08) e devolve um
resumo curto pronto - que e exatamente o tamanho de uma resposta falada.
Buscador generico devolveria pagina inteira para alguem resumir, e quem
resumiria seria o modelo, gastando a cota que este modulo existe para
poupar.

## O QUE ESTE MODULO NAO FAZ

Nao decide QUANDO buscar. Isso e do planejador - e proposital: dar ao robo
uma ferramenta e decidir por ele quando usa-la sao duas mudancas, e a
segunda merece ser discutida sozinha.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

logger = logging.getLogger("orion.mission.internet")

#: pt.wikipedia por padrao - a casa fala portugues.
IDIOMA_PADRAO = "pt"

#: Segundos. Curto de proposito: isto acontece no meio de uma conversa
#: falada, e uma pausa de 8 s no meio de uma frase e pior que nao saber.
TIMEOUT_PADRAO_S = 6.0

#: Tamanho do trecho que volta. Resposta falada longa cansa - e o robo ja
#: aprendeu que resposta curta e o que o faz falar logo.
MAXIMO_CARACTERES = 420

#: A Wikimedia EXIGE contato no User-Agent e devolve 403 sem ele - medido
#: em 07/08, com a mesma URL passando por curl e falhando por httpx so pela
#: falta desta linha. Nao e detalhe de etiqueta: sem contato, nao responde.
_USER_AGENT = "ORION-OS/0.1 (robo assistente domestico; jproma23@gmail.com)"


@dataclass(frozen=True)
class Achado:
    titulo: str
    trecho: str
    url: str
    fonte: str = "Wikipédia"

    def como_dict(self) -> dict:
        return {
            "titulo": self.titulo,
            "trecho": self.trecho,
            "url": self.url,
            "fonte": self.fonte,
        }

    def para_falar(self) -> str:
        """A frase pronta, COM a procedencia. E o unico jeito de sair daqui."""
        return f"Segundo a {self.fonte}, {self.trecho}"


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def _encurtar(texto: str, maximo: int = MAXIMO_CARACTERES) -> str:
    """Corta no fim de frase, nao no meio da palavra."""
    limpo = re.sub(r"\s+", " ", texto).strip()
    if len(limpo) <= maximo:
        return limpo
    corte = limpo[:maximo]
    ponto = corte.rfind(". ")
    if ponto > maximo // 3:
        return corte[: ponto + 1]
    espaco = corte.rfind(" ")
    return (corte[:espaco] if espaco > 0 else corte).rstrip(",;:") + "..."



def _melhor_pagina(paginas: list[dict], termo: str) -> dict | None:
    """Entre os candidatos, o titulo que MAIS casa com o que se perguntou.

    DEFEITO REAL que motivou isto (medido 07/08): pedindo `limit=1`, a
    pergunta "lingua espanhola" devolvia o artigo **"Língua espanhola nos
    Estados Unidos"**. A busca da Wikipedia ordena por relevancia dela, nao
    pela sua - e o primeiro resultado costuma ser um artigo mais especifico
    que o assunto perguntado.

    Isso e pior que nao achar nada: o robo responderia com confianca sobre
    o assunto errado, que e a familia de defeito que este projeto mais
    persegue.

    A regra: titulo IGUAL ao termo ganha; depois, o titulo mais CURTO entre
    os que comecam pelo termo (mais curto = mais generico = mais perto do
    que se perguntou); por ultimo, o primeiro da lista.
    """
    if not paginas:
        return None
    alvo = _sem_acento(termo).lower().strip()

    def _titulo(pagina: dict) -> str:
        return _sem_acento(str(pagina.get("title") or "")).lower().strip()

    exatos = [p for p in paginas if _titulo(p) == alvo]
    if exatos:
        return exatos[0]

    comecam = [p for p in paginas if _titulo(p).startswith(alvo)]
    if comecam:
        return min(comecam, key=lambda p: len(_titulo(p)))

    return paginas[0]


class ConsultaInternet:
    """Busca um resumo na Wikipédia. Nunca levanta - devolve None."""

    def __init__(
        self,
        idioma: str = IDIOMA_PADRAO,
        timeout_s: float = TIMEOUT_PADRAO_S,
        cliente=None,
    ) -> None:
        self._idioma = idioma
        self._timeout_s = timeout_s
        #: injetavel para os testes nao dependerem de rede
        self._cliente = cliente

    async def _pegar_json(self, url: str, parametros: dict | None = None):
        import httpx

        cliente = self._cliente
        if cliente is not None:
            resposta = await cliente.get(url, params=parametros)
            resposta.raise_for_status()
            return resposta.json()
        async with httpx.AsyncClient(
            timeout=self._timeout_s, headers={"User-Agent": _USER_AGENT}
        ) as http:
            resposta = await http.get(url, params=parametros)
            resposta.raise_for_status()
            return resposta.json()

    async def _achar_titulo(self, termo: str) -> str | None:
        """A busca por termo, que aceita o jeito falado de escrever.

        Usa o `rest.php/v1/search/title` e nao o `api.php?action=opensearch`:
        o moderno devolve estrutura (chave, titulo, descricao) em vez de
        quatro listas paralelas, e nao exige montar parametros de namespace.

        Tenta o termo COMO VEIO e depois SEM ACENTO. Quem fala com o robo
        nao acentua, e o Whisper tambem nao garante acento - medido em
        07/08, `sensor ultrassonico` sem acento devolvia vazio.
        """
        url = f"https://{self._idioma}.wikipedia.org/w/rest.php/v1/search/title"
        for tentativa in (termo, _sem_acento(termo)):
            dados = await self._pegar_json(url, {"q": tentativa, "limit": 5})
            paginas = (dados or {}).get("pages") or []
            escolhida = _melhor_pagina(paginas, tentativa)
            if escolhida:
                # `key` e o titulo ja no formato de URL (com underscore)
                return escolhida.get("key") or escolhida.get("title")
        return None

    async def _resumo_de(self, titulo: str) -> dict | None:
        """O resumo de um titulo, ou None se a pagina nao existir.

        404 aqui e resposta legitima ("nao existe artigo com esse nome"), e
        nao falha - por isso ele nao sobe como erro.
        """
        import httpx

        try:
            return await self._pegar_json(
                f"https://{self._idioma}.wikipedia.org/api/rest_v1/page/summary/"
                + titulo.strip().replace(" ", "_")
            )
        except httpx.HTTPStatusError as erro:
            if erro.response.status_code == 404:
                return None
            raise

    async def consultar(self, termo: str) -> Achado | None:
        """O resumo do assunto, ou None se nao houver (ou a rede falhar)."""
        termo = (termo or "").strip()
        if len(termo) < 3:
            return None
        try:
            # O TERMO COMO TITULO VEM PRIMEIRO, e a ordem importa: o
            # endpoint de resumo SEGUE REDIRECIONAMENTO, e a busca por
            # titulo nao. Em pt.wikipedia "Lingua espanhola" e redirect
            # para o artigo certo; pela busca, ela devolvia "Lingua
            # espanhola nos Estados Unidos" - resposta plausivel sobre o
            # assunto errado, que e pior que nao achar nada (medido 07/08).
            resumo = await self._resumo_de(termo)
            if resumo is None:
                titulo = await self._achar_titulo(termo)
                if not titulo:
                    logger.info("internet: nada encontrado para %r", termo)
                    return None
                resumo = await self._resumo_de(str(titulo))
                if resumo is None:
                    return None
            titulo = resumo.get("title") or termo
        except Exception:
            # Sem rede o robo simplesmente NAO SABE - que e uma resposta
            # honesta. Derrubar a conversa por causa disso seria trocar
            # "nao sei" por silencio.
            logger.warning("internet: consulta a %r falhou", termo, exc_info=True)
            return None

        extrato = (resumo or {}).get("extract") or ""
        if not extrato.strip():
            return None

        return Achado(
            titulo=(resumo.get("title") or titulo).strip(),
            trecho=_encurtar(extrato),
            url=((resumo.get("content_urls") or {}).get("desktop") or {}).get("page", ""),
        )
