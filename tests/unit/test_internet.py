"""Consulta a internet (2026-08-07).

Nenhum teste toca a rede: o cliente e injetado. O que eles guardam e o que
nao pode mudar sem alguem perceber - a PROCEDENCIA no que sai, e o robo
dizer "nao sei" em vez de inventar quando a rede falha.
"""

import httpx
import pytest

from orion.mission.internet import Achado, ConsultaInternet, _melhor_pagina


class _RespostaFalsa:
    def __init__(self, dados, status=200):
        self._dados = dados
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "erro", request=httpx.Request("GET", "http://x"), response=self
            )

    def json(self):
        return self._dados


class _ClienteFalso:
    """Devolve respostas na ordem, e registra o que foi pedido."""

    def __init__(self, respostas):
        self._respostas = list(respostas)
        self.pedidos = []

    async def get(self, url, params=None):
        self.pedidos.append((url, params))
        if not self._respostas:
            return _RespostaFalsa({}, status=404)
        return self._respostas.pop(0)


RESUMO = {
    "title": "Lidar",
    "extract": "Lidar é uma tecnologia óptica de deteção remota.",
    "content_urls": {"desktop": {"page": "https://pt.wikipedia.org/wiki/Lidar"}},
}


@pytest.mark.asyncio
async def test_o_que_sai_daqui_carrega_a_procedencia():
    """A regra do modulo: texto de fora nunca chega sem etiqueta."""
    consulta = ConsultaInternet(cliente=_ClienteFalso([_RespostaFalsa(RESUMO)]))

    achado = await consulta.consultar("Lidar")

    assert achado is not None
    assert achado.fonte == "Wikipédia"
    assert achado.url.startswith("https://")
    assert achado.para_falar().startswith("Segundo a Wikipédia,")


@pytest.mark.asyncio
async def test_o_termo_e_tentado_como_titulo_ANTES_da_busca():
    """A ordem importa: o resumo segue redirecionamento, a busca nao."""
    cliente = _ClienteFalso([_RespostaFalsa(RESUMO)])
    consulta = ConsultaInternet(cliente=cliente)

    await consulta.consultar("Lidar")

    primeira_url = cliente.pedidos[0][0]
    assert "page/summary" in primeira_url, "buscou antes de tentar o titulo direto"


@pytest.mark.asyncio
async def test_sem_pagina_direta_cai_para_a_busca():
    cliente = _ClienteFalso(
        [
            _RespostaFalsa({}, status=404),  # nao existe com esse titulo
            _RespostaFalsa({"pages": [{"key": "Lidar", "title": "Lidar"}]}),
            _RespostaFalsa(RESUMO),
        ]
    )
    consulta = ConsultaInternet(cliente=cliente)

    achado = await consulta.consultar("lidar tecnologia")

    assert achado is not None
    assert achado.titulo == "Lidar"


@pytest.mark.asyncio
async def test_rede_fora_devolve_nada_e_nao_derruba():
    """Sem rede o robo NAO SABE - que e resposta honesta, nao excecao."""

    class _Explode:
        async def get(self, url, params=None):
            raise httpx.ConnectError("sem rede")

    consulta = ConsultaInternet(cliente=_Explode())

    assert await consulta.consultar("Lidar") is None


@pytest.mark.asyncio
async def test_assunto_inexistente_devolve_nada():
    cliente = _ClienteFalso(
        [_RespostaFalsa({}, status=404), _RespostaFalsa({"pages": []})]
    )
    consulta = ConsultaInternet(cliente=cliente)

    assert await consulta.consultar("asdkjhasdkjh") is None


@pytest.mark.asyncio
async def test_resumo_vazio_nao_vira_resposta():
    """Pagina existe mas sem texto: melhor nao dizer nada."""
    consulta = ConsultaInternet(
        cliente=_ClienteFalso([_RespostaFalsa({"title": "X", "extract": "   "})])
    )

    assert await consulta.consultar("X") is None


@pytest.mark.asyncio
async def test_termo_curto_demais_nem_consulta():
    cliente = _ClienteFalso([])
    consulta = ConsultaInternet(cliente=cliente)

    assert await consulta.consultar("a") is None
    assert cliente.pedidos == [], "gastou rede com termo de uma letra"


@pytest.mark.asyncio
async def test_trecho_longo_e_cortado_no_fim_de_frase():
    """Resposta falada longa cansa - e cortar no meio da palavra e pior."""
    longo = ("Uma frase completa que se repete. " * 40).strip()
    consulta = ConsultaInternet(
        # O titulo bate com o termo procurado de proposito: desde 07/08 a
        # consulta recusa artigo sem relacao nenhuma com o que se pediu, e
        # este teste e sobre CORTAR o trecho, nao sobre pertinencia.
        cliente=_ClienteFalso([_RespostaFalsa({"title": "Assunto", "extract": longo})])
    )

    achado = await consulta.consultar("assunto")

    assert achado is not None
    assert len(achado.trecho) <= 420
    assert achado.trecho.endswith((".", "...")), "cortou no meio da palavra"


def test_titulo_exato_ganha_do_mais_relevante():
    """O DEFEITO que motivou `_melhor_pagina`.

    A busca ordena pela relevancia DELA: perguntando "lingua espanhola" o
    primeiro resultado era "Língua espanhola nos Estados Unidos" - resposta
    plausivel sobre o assunto errado, pior que nao achar nada.
    """
    paginas = [
        {"title": "Língua espanhola nos Estados Unidos", "key": "A"},
        {"title": "Língua espanhola", "key": "B"},
    ]

    assert _melhor_pagina(paginas, "lingua espanhola")["key"] == "B"


def test_sem_exato_prefere_o_titulo_mais_curto():
    """Mais curto = mais generico = mais perto do que se perguntou.

    Perguntando "lidar", os dois titulos servem - mas quem pergunta pelo
    assunto quer o artigo do assunto, nao a aplicacao dele na arqueologia.
    """
    paginas = [
        {"title": "Lidar na arqueologia", "key": "A"},
        {"title": "Lidar", "key": "B"},
    ]

    assert _melhor_pagina(paginas, "lidar")["key"] == "B"


def test_nenhum_titulo_casa_fica_com_a_ordem_da_busca():
    """Sem casamento, respeitar a relevancia de quem buscou e o certo.

    Inventar criterio proprio aqui seria pior: a busca ordenou com o indice
    inteiro, e este modulo tem cinco titulos na mao.
    """
    paginas = [
        {"title": "Lidar na arqueologia", "key": "A"},
        {"title": "Lidar", "key": "B"},
    ]

    assert _melhor_pagina(paginas, "varredura a laser")["key"] == "A"


def test_melhor_pagina_sem_candidatos():
    assert _melhor_pagina([], "qualquer") is None


def test_como_dict_leva_a_fonte():
    """Quem consumir isto pela ponte TCP tem de receber a procedencia."""
    d = Achado("T", "trecho", "https://x").como_dict()

    assert d["fonte"] == "Wikipédia"
    assert set(d) == {"titulo", "trecho", "url", "fonte"}
