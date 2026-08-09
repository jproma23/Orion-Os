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


def test_pergunta_do_mundo_com_voce_dentro_nao_e_sequestrada(saber):
    """Achado em uso real, 2026-08-07.

    "Voce me explica porque o ceu e azul?" era respondida com o fato da
    TELA, porque a palavra "explicando" aparece no meio da resposta dele. A
    pergunta tem "voce", entao o portao deixa passar - e o portao esta
    certo. O errado era casar no corpo da resposta em vez de nas perguntas.
    """
    assert saber.responder("voce me explica porque o ceu e azul?") is None
    assert saber.responder("voce sabe quem descobriu o Brasil?") is None


def test_palavra_generica_solta_nao_sequestra(saber):
    """Achado em uso real, 2026-08-08 - as frases sao as do chat.

    Uma palavra generica ("consegue", "lado", "nao") encostava num fato e a
    resposta pronta saia com confianca no lugar da conversa. Pior: COMANDOS
    de movimento viravam palestra sobre o corpo, e o robo narrava em vez de
    obedecer.
    """
    # "consegue" casava com "o que voce consegue ligar" -> fato da LUZ
    assert saber.responder("Eu acho. Você consegue fazer isso?") is None
    assert (
        saber.responder(
            "Isso é inteligência, você consegue usar isso futuramente "
            "quando você quiser."
        )
        is None
    )
    # "nao" casava com o titulo "O que eu NAO tenho" -> fato dos BRACOS
    assert (
        saber.responder(
            "eu vou lidar essas capacidades intrínsecas, "
            "você ainda não tem essa capacidade"
        )
        is None
    )
    # pergunta emocional, nao anatomica: quem responde e a conversa
    assert saber.responder("Você sente falta de ter braço?") is None


def test_comando_de_movimento_nao_vira_palestra(saber):
    """Achado em uso real, 2026-08-08.

    "gire para a esquerda" casava com "lado" em "olha para os lados" e o
    robo explicava os servos em vez de girar. Comando tem que seguir para a
    cascata, que e quem sabe executar.
    """
    assert saber.responder("Eu quero que você gire para o lado esquerdo") is None
    assert (
        saber.responder("Eu quero que você rode ao redor do seu eixo com as rodas")
        is None
    )


def test_pergunta_de_verdade_sobre_ele_continua_respondida(saber):
    """A cobertura nao pode fechar a porta para quem pergunta do corpo."""
    resposta = saber.responder("o que voce consegue ligar")
    assert resposta is not None
    assert "ventilador" in resposta
    assert saber.responder("voce tem camera?") is not None
    assert saber.responder("voce nao tem camera?") is not None


def test_banco_vazio_nao_responde(tmp_path):
    vazio = Autoconhecimento(tmp_path / "vazio.db")
    assert vazio.responder("como voce anda") is None
    vazio.fechar()
