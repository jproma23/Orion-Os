"""Testes do caminho local ate as acoes (2026-08-07).

O que protegem: com a IA fora, o robo AINDA obedece - e nao obedece ao que
nao foi pedido, que e o risco de todo casador de frase.
"""
import pytest

from orion.mission.acoes_faladas import ACOES
from orion.mission.comando_local import GATILHOS, SEM_GATILHO_DE_PROPOSITO, casar


@pytest.mark.parametrize(
    "frase,esperado",
    [
        ("acende a luz", "acender_luz"),
        ("Fofão, apaga a luz por favor", "apagar_luz"),
        ("liga o ventilador", "ligar_ventilador"),
        ("olha para a esquerda", "olhar_esquerda"),
        ("olha pra cima", "olhar_cima"),
        ("vira a direita", "virar_direita"),
        ("anda pra frente", "andar_para_frente"),
        ("varre a frente", "varrer_a_frente"),
    ],
)
def test_obedece_sem_ia(frase, esperado):
    casamento = casar(frase)
    assert casamento is not None, f"nao entendeu {frase!r}"
    assert casamento.acao.nome == esperado


def test_acento_e_pontuacao_nao_atrapalham():
    """A transcricao do Whisper varia na mesma frase dita duas vezes."""
    assert casar("Olha para a esquerda!").acao.nome == "olhar_esquerda"
    assert casar("acende a lanterna.").acao.nome == "acender_luz"


def test_nao_casa_dentro_de_outra_palavra():
    """O defeito que este projeto ja tomou: 'parar' casa dentro de 'preparar'.

    Aqui todo gatilho tem limite de palavra - sem ele, qualquer frase com
    "aluz" ou "esquerdas" acionaria hardware.
    """
    assert casar("a paluz esta boa") is None
    assert casar("liga a luzimar") is None


def test_negacao_nao_aciona():
    """'nao acende a luz' nao pode acender a luz."""
    assert casar("nao acende a luz") is None
    assert casar("por favor nao acende a luz") is None


def test_conversa_normal_nao_vira_comando():
    assert casar("como foi seu dia") is None
    assert casar("voce esta bem") is None
    assert casar("bom dia Fofao") is None


def test_busca_leva_o_assunto_junto():
    casamento = casar("busca sobre o planeta Marte")
    assert casamento is not None
    assert casamento.acao.nome == "buscar"
    assert "marte" in casamento.argumento


def test_pergunta_de_conhecimento_vira_busca():
    """Com a IA fora, "o que e fotossintese" ainda tem resposta: a internet."""
    casamento = casar("o que e fotossintese")
    assert casamento is not None and casamento.acao.nome == "buscar"


def test_busca_sem_assunto_nao_casa():
    assert casar("o que e") is None


def test_o_gatilho_mais_longo_vence():
    casamento = casar("olha para a esquerda")
    assert casamento.gatilho == "olha para a esquerda"


def test_recado_nao_tem_caminho_local():
    """De proposito: errar um recado cria uma PROMESSA FALSA.

    O modulo de recados nasceu de uma promessa dessas. Sem modelo, dizer que
    nao consegue e melhor que prometer avisar alguem e nao avisar.
    """
    assert "deixar_recado" in SEM_GATILHO_DE_PROPOSITO
    assert casar("avise a Mara que eu quero falar com ela") is None


def test_toda_acao_do_catalogo_tem_caminho_local_ou_motivo():
    """A conferencia que impede a tabela de envelhecer calada."""
    from orion.mission.comando_local import GATILHOS_COM_ASSUNTO

    cobertas = set(GATILHOS) | set(GATILHOS_COM_ASSUNTO) | SEM_GATILHO_DE_PROPOSITO
    assert set(ACOES) <= cobertas
