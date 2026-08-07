"""Testes do quadro e do banco de aulas (2026-08-07).

O que estes testes protegem: o conteudo de verdade em `content/aulas/` e
valido, a busca acha pelo que a PESSOA fala (e nao pelo titulo exato), e o
vocabulario do quadro e mesmo fechado.
"""
from pathlib import Path

import pytest

from orion.display import quadro as _quadro
from orion.ensino.banco_aulas import (
    AulaInvalida,
    BancoAulas,
    carregar_arquivo,
    carregar_pasta,
    normalizar_nivel,
)

PASTA_CONTEUDO = Path(__file__).resolve().parents[2] / "content" / "aulas"


# ----------------------------------------------------------------- quadro
def test_tipo_desconhecido_nao_vira_bloco():
    """A trava central: o que a tela nao sabe desenhar nao entra."""
    with pytest.raises(_quadro.BlocoInvalido):
        _quadro.montar("videozinho", texto="oi")


def test_campo_obrigatorio_faltando_e_recusado():
    with pytest.raises(_quadro.BlocoInvalido):
        _quadro.montar("titulo")


def test_campo_inventado_e_recusado():
    """'titulo=' em vez de 'texto=' apareceria como bloco vazio, calado."""
    with pytest.raises(_quadro.BlocoInvalido):
        _quadro.montar("texto", titulo="oi")


def test_o_prompt_do_quadro_descreve_todos_os_tipos():
    bloco = _quadro.bloco_para_prompt()
    for nome in _quadro.TIPOS:
        assert nome in bloco


class BusFalso:
    def __init__(self):
        self.publicados = []

    async def publish(self, topico, dados=None, **kwargs):
        self.publicados.append((topico, dados))


@pytest.mark.asyncio
async def test_escrever_numera_os_blocos_e_acende_a_tela():
    bus = BusFalso()
    quadro = _quadro.Quadro(bus)
    primeiro = await quadro.escrever("titulo", texto="Aula 1")
    segundo = await quadro.escrever("texto", texto="Explicacao")
    assert (primeiro, segundo) == ("b1", "b2")
    topicos = [t for t, _ in bus.publicados]
    assert topicos.count(_quadro.TOPICO_TELA) == 2, "o quadro tem que acender a tela"
    assert topicos.count(_quadro.TOPICO) == 2


# ------------------------------------------------------------ conteudo real
def test_todo_o_conteudo_de_aulas_e_valido():
    """Le `content/aulas/` de verdade.

    E o teste que impede uma aula quebrada de chegar ao aluno: bloco com
    tipo errado ou passo sem fala derruba AQUI, e nao no meio da explicacao.
    """
    aulas = carregar_pasta(PASTA_CONTEUDO)
    assert aulas, "nenhuma aula em content/aulas"
    for aula in aulas:
        assert aula.passos, f"{aula.id} sem passos"
        assert aula.resumo, f"{aula.id} sem resumo - a busca depende dele"


def test_aula_com_nivel_inventado_e_recusada(tmp_path):
    arquivo = tmp_path / "x.yaml"
    arquivo.write_text(
        "id: a.b.c\nmateria: teste\ntitulo: T\nnivel: meio-avancado\n"
        "passos:\n  - fala: oi\n",
        encoding="utf-8",
    )
    with pytest.raises(AulaInvalida):
        carregar_arquivo(arquivo)


def test_aula_com_bloco_que_a_tela_nao_desenha_e_recusada(tmp_path):
    arquivo = tmp_path / "x.yaml"
    arquivo.write_text(
        "id: a.b.c\nmateria: teste\ntitulo: T\nnivel: basico\n"
        "passos:\n  - fala: oi\n    quadro:\n      tipo: hologramas\n      texto: x\n",
        encoding="utf-8",
    )
    with pytest.raises(AulaInvalida) as erro:
        carregar_arquivo(arquivo)
    assert "passo 1" in str(erro.value), "o erro precisa dizer QUAL passo"


# --------------------------------------------------------------- busca
@pytest.fixture
def banco(tmp_path):
    banco = BancoAulas(tmp_path / "saber.db")
    banco.construir(carregar_pasta(PASTA_CONTEUDO))
    yield banco
    banco.fechar()


@pytest.mark.parametrize(
    "pedido,esperado",
    [
        ("me da uma aula de ingles", "ingles"),
        ("quero aprender equacao do segundo grau", "matematica"),
        ("me ensina python", "programacao"),
        ("aula sobre bhaskara", "matematica"),      # palavra do CORPO, nao do titulo
        ("aula de inglês", "ingles"),               # com acento, como esta no conteudo
    ],
)
def test_busca_acha_pelo_que_a_pessoa_fala(banco, pedido, esperado):
    achados = banco.buscar(pedido)
    assert achados, f"nao achou nada para {pedido!r}"
    assert achados[0].materia == esperado


def test_busca_sem_resposta_devolve_vazio_e_nao_chuta(banco):
    """Nao achar e a resposta honesta - e o degrau seguinte da cascata."""
    assert banco.buscar("culinaria tailandesa") == []


def test_busca_respeita_o_nivel(banco):
    achados = banco.buscar("ingles", nivel="intermediario")
    assert achados and all(a.nivel == "intermediario" for a in achados)


@pytest.mark.parametrize(
    "frase,nivel",
    [
        ("me da uma aula basica de ingles", "basico"),
        ("quero uma aula avancada", "avancado"),
        ("aula intermediaria de matematica", "intermediario"),
        ("me da uma aula de ingles", None),
    ],
)
def test_nivel_e_lido_da_fala(frase, nivel):
    assert normalizar_nivel(frase) == nivel
