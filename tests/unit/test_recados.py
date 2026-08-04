"""Recados: guardar e entregar quando a pessoa chega.

O que estes testes protegem e uma PROMESSA. Em 2026-08-03 o robo disse que
avisaria a Mara, cumprimentou a Mara e nao avisou - e nada em lugar nenhum
registrou falha, porque nao havia o que falhar. Cada teste aqui fecha um dos
pontos onde aquele silencio poderia voltar.
"""
from __future__ import annotations

import pytest

from orion.mission import recados
from orion.mission.recados import Recados, mesmo_nome


class MemoriaFalsa:
    """Banco de mentira com o suficiente para `remember`/`recall`/`update`."""

    def __init__(self, falhar: str = "") -> None:
        self.linhas: list[dict] = []
        self._proximo_id = 1
        self._falhar = falhar

    async def remember(self, categoria, dados):
        if self._falhar == "remember":
            raise RuntimeError("SSD desmontado (simulado)")
        linha = dict(dados, id=self._proximo_id, categoria=categoria)
        self._proximo_id += 1
        self.linhas.append(linha)
        return linha["id"]

    async def recall(self, categoria, filtro=None, limite=20):
        if self._falhar == "recall":
            raise RuntimeError("link com o Pi caiu (simulado)")
        saida = [linha for linha in self.linhas if linha["categoria"] == categoria]
        for chave, valor in (filtro or {}).items():
            saida = [linha for linha in saida if linha.get(chave) == valor]
        return saida[:limite]

    async def update(self, categoria, id, dados):
        for linha in self.linhas:
            if linha["id"] == id:
                linha.update(dados)
                return True
        return False


def _recados(memoria, momento="2026-08-03T23:00:00"):
    return Recados(memoria, agora=lambda: momento)


@pytest.mark.asyncio
async def test_recado_guardado_aparece_para_quem_chega():
    memoria = MemoriaFalsa()
    caixa = _recados(memoria)

    assert await caixa.deixar("Mara", "o Joao quer falar com voce") is True
    pendentes = await caixa.pendentes("Mara")

    assert [r["texto"] for r in pendentes] == ["o Joao quer falar com voce"]


@pytest.mark.asyncio
async def test_recado_entregue_nao_volta():
    """Sem isto o robo repetiria o mesmo recado a cada chegada, para sempre."""
    memoria = MemoriaFalsa()
    caixa = _recados(memoria)
    await caixa.deixar("Mara", "o Joao quer falar")

    (recado,) = await caixa.pendentes("Mara")
    await caixa.marcar_entregue(recado["id"])

    assert await caixa.pendentes("Mara") == []


@pytest.mark.asyncio
async def test_entregar_nao_apaga_o_registro():
    """O registro e a prova de que ele cumpriu o combinado. Apagar na entrega
    deixaria o robo sem como responder "voce avisou a Mara?"."""
    memoria = MemoriaFalsa()
    caixa = _recados(memoria)
    await caixa.deixar("Mara", "o Joao quer falar")
    (recado,) = await caixa.pendentes("Mara")

    await caixa.marcar_entregue(recado["id"])

    assert len(memoria.linhas) == 1
    assert memoria.linhas[0]["entregue_em"] == "2026-08-03T23:00:00"


@pytest.mark.asyncio
async def test_recado_de_outra_pessoa_nao_e_entregue():
    memoria = MemoriaFalsa()
    caixa = _recados(memoria)
    await caixa.deixar("Mara", "recado da Mara")
    await caixa.deixar("Kamal", "recado do Kamal")

    pendentes = await caixa.pendentes("Kamal")

    assert [r["texto"] for r in pendentes] == ["recado do Kamal"]


@pytest.mark.asyncio
async def test_o_mais_antigo_sai_primeiro():
    memoria = MemoriaFalsa()
    await Recados(memoria, agora=lambda: "2026-08-01T10:00:00").deixar("Mara", "primeiro")
    await Recados(memoria, agora=lambda: "2026-08-03T10:00:00").deixar("Mara", "segundo")

    pendentes = await _recados(memoria).pendentes("Mara")

    assert [r["texto"] for r in pendentes] == ["primeiro", "segundo"]


@pytest.mark.asyncio
async def test_recado_vazio_nao_e_guardado():
    """Prometer guardar "nada para ninguem" e prometer o que nao se cumpre."""
    memoria = MemoriaFalsa()
    caixa = _recados(memoria)

    assert await caixa.deixar("", "alguma coisa") is False
    assert await caixa.deixar("Mara", "") is False
    assert memoria.linhas == []


@pytest.mark.asyncio
async def test_banco_fora_do_ar_devolve_False_em_vez_de_explodir():
    """O robo tem que SABER que nao guardou - e o retorno e o unico jeito.
    Levantar aqui derrubaria a conversa; devolver True mentiria."""
    caixa = _recados(MemoriaFalsa(falhar="remember"))

    assert await caixa.deixar("Mara", "o Joao quer falar") is False


@pytest.mark.asyncio
async def test_falha_ao_consultar_nao_impede_o_cumprimento():
    """Perder um recado e ruim; deixar de cumprimentar por causa disso e pior -
    e seria uma falha visivel causada por outra invisivel."""
    caixa = _recados(MemoriaFalsa(falhar="recall"))

    assert await caixa.pendentes("Mara") == []


class TestCasamentoDeNome:
    """"Marah" no orion.yaml, "Mara" na fala do dono, "mara" no banco."""

    def test_apelido_cortado_casa(self):
        assert mesmo_nome("Marah", "Mara")
        assert mesmo_nome("Mara", "Marah")

    def test_acento_e_caixa_nao_importam(self):
        assert mesmo_nome("JOÃO", "joao")

    def test_primeiro_nome_basta(self):
        assert mesmo_nome("Joao Paulo", "Joao")

    def test_pessoas_diferentes_nao_casam(self):
        assert not mesmo_nome("Mario", "Mara")
        assert not mesmo_nome("Kamal", "Marah")

    def test_nome_curto_exige_igualdade(self):
        """Prefixo de duas letras casaria com meio mundo."""
        assert not mesmo_nome("Al", "Alberto")
        assert mesmo_nome("Al", "al")


# ---------------------------------------------------------------------------
# APELIDOS DECLARADOS (2026-08-04)
#
# A Marah atende por Marah, Mara OU Marisane. "Mara" ja casava por prefixo;
# "Marisane" nao casa com nada - um recado deixado para ela sumia, guardado com
# um nome e procurado com outro. Nenhuma regra de string descobre isso: sao a
# mesma pessoa porque o dono DIZ que sao.
# ---------------------------------------------------------------------------

_FAMILIA_DA_CASA = [
    {"nome": "Marah", "apelidos": ["Mara", "Marisane"]},
    {"nome": "Kamal"},
]


@pytest.fixture
def familia_carregada():
    """Carrega e DESCARREGA - a tabela e global, e um teste nao pode vazar
    apelidos para o proximo."""
    recados.definir_apelidos(_FAMILIA_DA_CASA)
    yield
    recados.definir_apelidos([])


def test_apelido_declarado_casa_mesmo_sem_parecer(familia_carregada):
    """O caso que motivou tudo: Marisane e Marah nao se parecem em nada."""
    assert recados.mesmo_nome("Marisane", "Marah")
    assert recados.mesmo_nome("Marah", "Marisane")
    # E entre dois apelidos da MESMA pessoa, sem passar pelo nome oficial.
    assert recados.mesmo_nome("Mara", "Marisane")


def test_apelido_nao_junta_quem_nao_e_da_mesma_pessoa(familia_carregada):
    """A trava contra o efeito colateral. Entregar recado a mais parecia
    barato ate existir uma tabela que pode ligar duas pessoas por engano - o
    recado do dono para a Marah nao pode ser lido para o Kamal, que tem 3
    anos."""
    assert not recados.mesmo_nome("Kamal", "Marah")
    assert not recados.mesmo_nome("Marisane", "Kamal")
    # "Mario" continua sendo outra pessoa, como sempre foi.
    assert not recados.mesmo_nome("Mario", "Marah")


def test_sem_familia_carregada_continua_o_comportamento_antigo():
    """Ferramentas de bancada e testes antigos constroem os recados sem
    config nenhuma. Sem apelidos, vale so o prefixo - e "Marisane" volta a
    ser outra pessoa, que e o certo quando ninguem declarou o contrario."""
    recados.definir_apelidos([])
    assert recados.mesmo_nome("Mara", "Marah")
    assert not recados.mesmo_nome("Marisane", "Marah")
