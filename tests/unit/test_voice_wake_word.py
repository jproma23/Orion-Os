"""Testes da deteccao da palavra de ativacao (Cap 9 s.3-4). Sem dependencias pesadas."""
from orion.voice.wake_word import DetectorPalavraAtivacao


def test_detecta_fofao_com_acento():
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("Fofão, que horas são?") is True


def test_detecta_fofao_sem_acento():
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("fofao voce esta ai") is True


def test_nao_detecta_sem_a_palavra():
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("oi tudo bem com voce") is False


def test_nao_detecta_substring_dentro_de_outra_palavra():
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("fofaozinho gostoso") is False


def test_palavra_customizada():
    detector = DetectorPalavraAtivacao(palavras_ativacao=("robo",))
    assert detector.verificar("ei robo, vem aqui") is True
    assert detector.verificar("fofao, oi") is False


def test_case_insensitive():
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("FOFAO responde ai") is True
