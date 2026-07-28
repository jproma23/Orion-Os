"""Testes do rastreamento de alvo (Cap 8 s.3, 6). Sem dependencias pesadas."""
from orion.vision.rastreamento import Rastreador


def test_sem_candidatos_nao_ha_alvo():
    rastreador = Rastreador()
    assert rastreador.atualizar([]) is None


def test_primeiro_alvo_e_a_maior_caixa():
    rastreador = Rastreador()
    pequena = (0, 0, 10, 10)
    grande = (100, 100, 300, 300)

    alvo = rastreador.atualizar([pequena, grande])

    assert alvo.caixa == grande


def test_mantem_o_alvo_mais_proximo_do_anterior():
    rastreador = Rastreador()
    rastreador.atualizar([(100, 100, 200, 200)], agora=0.0)

    candidatos = [(400, 400, 500, 500), (105, 105, 205, 205)]
    alvo = rastreador.atualizar(candidatos, agora=0.1)

    assert alvo.caixa == (105, 105, 205, 205)


def test_identidade_associada_a_caixa():
    rastreador = Rastreador()
    caixa = (10, 10, 50, 50)
    identidades = {caixa: (3, "Joao")}

    alvo = rastreador.atualizar([caixa], identidades)

    assert alvo.pessoa_id == 3
    assert alvo.nome == "Joao"


def test_alvo_perdido_apos_timeout_sem_deteccoes():
    rastreador = Rastreador(timeout_perdido_s=1.0)
    rastreador.atualizar([(0, 0, 10, 10)], agora=0.0)

    # ainda dentro do timeout - mantem o ultimo alvo conhecido
    assert rastreador.atualizar([], agora=0.5) is not None
    assert rastreador.perdido(agora=0.5) is False

    # timeout estourado - considera perdido
    assert rastreador.atualizar([], agora=2.0) is None
    assert rastreador.perdido(agora=2.0) is True


def test_perdido_antes_de_qualquer_deteccao():
    rastreador = Rastreador()
    assert rastreador.perdido() is True


def test_exige_frames_de_confirmacao_antes_de_assumir_alvo_novo():
    """Mitigacao de falso positivo (2026-07-28): uma deteccao isolada nao
    vira alvo; precisa se repetir na MESMA regiao por N quadros seguidos."""
    rastreador = Rastreador(frames_confirmacao=3)
    caixa = (100, 100, 200, 200)

    assert rastreador.atualizar([caixa], agora=0.0) is None
    assert rastreador.atualizar([caixa], agora=0.1) is None
    alvo = rastreador.atualizar([caixa], agora=0.2)

    assert alvo is not None
    assert alvo.caixa == caixa


def test_candidata_diferente_reinicia_a_contagem_de_confirmacao():
    """Um falso positivo passageiro nao deve somar contagem com a proxima
    deteccao de verdade numa regiao diferente."""
    rastreador = Rastreador(frames_confirmacao=3)
    fantasma = (0, 0, 10, 10)
    pessoa = (300, 300, 400, 400)

    assert rastreador.atualizar([fantasma], agora=0.0) is None
    assert rastreador.atualizar([pessoa], agora=0.1) is None  # regiao diferente, reinicia
    assert rastreador.atualizar([pessoa], agora=0.2) is None
    alvo = rastreador.atualizar([pessoa], agora=0.3)

    assert alvo is not None
    assert alvo.caixa == pessoa
