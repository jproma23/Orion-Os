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
