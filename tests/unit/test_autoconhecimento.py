"""Testes do autoconhecimento (2026-08-07).

O que protegem: ele responde sobre o proprio corpo sem IA, NAO sequestra
conversa que nao e sobre ele, e nunca responde "quase isso".
"""
from pathlib import Path

import pytest

from orion.ensino.autoconhecimento import Autoconhecimento, carregar_arquivo

ARQUIVO = Path(__file__).resolve().parents[2] / "content" / "sobre_mim.yaml"


@pytest.fixture
def saber(tmp_path):
    banco = Autoconhecimento(tmp_path / "saber.db")
    banco.construir(carregar_arquivo(ARQUIVO))
    yield banco
    banco.fechar()


def test_o_arquivo_de_verdade_e_valido():
    fatos = carregar_arquivo(ARQUIVO)
    assert len(fatos) >= 8
    for fato in fatos:
        assert fato.perguntas, f"{fato.id} sem perguntas - ninguem o acharia"


@pytest.mark.parametrize(
    "pergunta,esperado",
    [
        ("como voce anda?", "motores"),
        ("quantas rodas voce tem?", "rodas"),
        ("como voce mexe a sua cabeca?", "servos"),
        ("voce tem bracos?", "nao tenho bracos"),
        ("como voce me escuta?", "microfones"),
        ("qual e o seu corpo?", "ESP32"),
        ("voce tem memoria?", "guardo"),
        ("por que sua tela apaga?", "energia"),
    ],
)
def test_responde_sobre_si_mesmo(saber, pergunta, esperado):
    resposta = saber.responder(pergunta)
    assert resposta is not None, f"nao respondeu {pergunta!r}"
    assert esperado.lower() in resposta.lower()


def test_nao_sequestra_conversa_que_nao_e_sobre_ele(saber):
    """O portao: sem ele, 'como anda a obra' cairia na resposta das rodas.

    Sequestrar conversa e o defeito que fez o classificador de intencao ser
    recusado neste projeto.
    """
    assert saber.responder("como anda a obra la em casa") is None
    assert saber.responder("quantas rodas tem um caminhao") is None
    assert saber.responder("bom dia") is None


def test_pergunta_sobre_ele_sem_fato_bom_nao_inventa(saber):
    """Melhor mandar para a IA do que responder outra coisa com confianca."""
    assert saber.responder("qual e a sua comida preferida") is None


def test_banco_vazio_nao_responde(tmp_path):
    vazio = Autoconhecimento(tmp_path / "vazio.db")
    assert vazio.responder("como voce anda") is None
    vazio.fechar()
