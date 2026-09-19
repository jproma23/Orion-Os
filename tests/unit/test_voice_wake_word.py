"""Testes da deteccao da palavra de ativacao (Cap 9 s.3-4). Sem dependencias pesadas."""
from orion.voice.wake_word import DetectorPalavraAtivacao


def test_detecta_javis():
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("Javis, que horas são?") is True


def test_detecta_javis_no_meio_da_frase():
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("javis voce esta ai") is True


def test_detecta_jarvis_com_o_erre():
    """O robo se chama Javis, mas o Whisper quase sempre transcreve com o
    'r' (a palavra que ele conhece e "Jarvis"), e quem fala tambem varia
    entre as duas. As DUAS acordam o robo: "jarvis" esta na lista de
    palavras de ativacao, e mesmo que nao estivesse a distancia de edicao
    entre elas e 1."""
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("Jarvis, que dia e hoje?") is True


def test_nao_detecta_sem_a_palavra():
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("oi tudo bem com voce") is False


def test_nao_detecta_substring_dentro_de_outra_palavra():
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("javiszinho gostoso") is False


def test_palavra_customizada():
    detector = DetectorPalavraAtivacao(palavras_ativacao=("robo",))
    assert detector.verificar("ei robo, vem aqui") is True
    assert detector.verificar("javis, oi") is False


def test_case_insensitive():
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("JAVIS responde ai") is True


def test_detecta_palavra_quebrada_em_dois_tokens():
    """Achado real, testado ao vivo em 2026-07-24 com a palavra de entao
    ('FO FÃO'): o Whisper as vezes transcreve o nome com um espaco no
    meio, o que quebrava o casamento por borda de palavra. O defeito e da
    transcricao, nao da palavra - vale igual para "ja vis"."""
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("JA VIS, que dia e hoje?") is True
    assert detector.verificar("ja vis") is True


def test_detecta_com_pequeno_erro_de_transcricao():
    """Tolerancia a 1 letra de diferenca (troca/falta/sobra) - pega
    transcricao quase certa sem abrir mao de exigir a palavra de
    verdade."""
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("javes, oi") is True  # 1 letra trocada
    assert detector.verificar("javi, oi") is True  # falta 1 letra


def test_nao_detecta_transcricoes_muito_diferentes():
    """Mesmo criterio catalogado ao vivo em 2026-07-24: transcricao
    foneticamente longe demais nao dispara, senao qualquer palavra curta
    do dia a dia acordaria o robo. Nesses casos a pessoa so repete o
    nome - o que ja acontecia na pratica."""
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("janela") is False
    assert detector.verificar("javali") is False
    assert detector.verificar("Ei, oui!") is False


def test_fuzzy_nao_dispara_em_palavra_curta_qualquer():
    """A tolerancia fonetica (passo 3) e restrita a tokens de tamanho
    parecido com 'javis' - nao pode disparar em qualquer frase comum do
    dia a dia."""
    detector = DetectorPalavraAtivacao()
    assert detector.verificar("oi, tudo bem com voce") is False
    assert detector.verificar("bom dia, como vai") is False
    assert detector.verificar("vamos tomar cafe") is False
