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


def test_detecta_fofao_quebrado_em_dois_tokens():
    """Achado real, testado ao vivo em 2026-07-24: o Whisper as vezes
    transcreve 'Fofão' com um espaco no meio ('FO FÃO'), o que quebrava
    o casamento por borda de palavra."""
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("FO FÃO, que dia e hoje?") is True
    assert detector.verificar("fo fao") is True


def test_detecta_fofao_com_pequeno_erro_de_transcricao():
    """Tolerancia a 1 letra de diferenca (troca/falta/sobra) - pega
    transcricao quase certa sem abrir mao de exigir a palavra de
    verdade."""
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("fofa, oi") is True  # falta 1 letra (deletion)
    assert detector.verificar("fofeo, oi") is True  # 1 letra trocada (substitution)


def test_nao_detecta_transcricoes_muito_diferentes():
    """Catalogado ao vivo em 2026-07-24 (mesma sessao de teste, varias
    tentativas ate o Whisper acertar 'Fofao' de verdade): essas
    transcricoes sao foneticamente longe demais pra pegar sem arriscar
    falso positivo em qualquer palavra curta do dia a dia - o usuario so
    precisa repetir a palavra de ativacao nesses casos, o que ja
    aconteceu na pratica."""
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("Fulfo") is False
    assert detector.verificar("FAMO") is False
    assert detector.verificar("Ei, oui!") is False


def test_fuzzy_nao_dispara_em_palavra_curta_qualquer():
    """A tolerancia fonetica (passo 3) e restrita a tokens de tamanho
    parecido com 'fofao' - nao pode disparar em qualquer frase comum do
    dia a dia."""
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("oi, tudo bem com voce") is False
    assert detector.verificar("bom dia, como vai") is False
