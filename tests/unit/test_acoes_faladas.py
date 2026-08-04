"""O catalogo de acoes faladas e a leitura do marcador.

O que estes testes protegem e a fronteira entre TEXTO e COMANDO. Ela e fina de
proposito - uma linha no comeco da resposta - e do lado errado dela estao os
dois modos de falha que importam: o robo falar "sustenido acao dois pontos" em
voz alta, e o robo obedecer a uma frase que so FALAVA sobre um comando.
"""
from orion.mission.acoes_faladas import ACOES, MARCADOR, bloco_para_prompt, extrair


def test_marcador_no_comeco_vira_acao_e_some_do_texto():
    acao, falado = extrair("#acao:olhar_direita\nOlhando pra direita.")

    assert acao is not None
    assert acao.nome == "olhar_direita"
    assert falado == "Olhando pra direita."


def test_marcador_no_MEIO_do_texto_e_texto_e_nao_comando():
    """`\\A` e nao `^`: se o robo esta EXPLICANDO como funciona, ou lendo em voz
    alta o que alguem escreveu, isso nao pode mover servo nenhum. Comando so no
    comeco - e a mesma razao por que o `parar` exige `\\b`."""
    texto = "Funciona assim: eu escrevo #acao:olhar_direita e a cabeca vira."

    acao, falado = extrair(texto)

    assert acao is None
    assert falado == texto


def test_conversa_normal_passa_intacta():
    acao, falado = extrair("Tudo bem sim, e voce?")

    assert acao is None
    assert falado == "Tudo bem sim, e voce?"


def test_acao_desconhecida_nao_vira_chute_e_o_marcador_some():
    """Regra 6 do contrato, aplicada aqui: fora do catalogo = nada acontece.
    Mas o marcador sai do texto assim mesmo - alucinacao do modelo nao pode
    virar ruido no alto-falante."""
    acao, falado = extrair("#acao:teletransportar\nJa fui.")

    assert acao is None
    assert MARCADOR not in falado
    assert falado == "Ja fui."


def test_so_o_marcador_sem_frase_deixa_o_texto_vazio():
    """Quem chama precisa distinguir "nao ha o que falar" de "falar isto" -
    senao o sintetizador carrega o Piper para produzir silencio."""
    acao, falado = extrair("#acao:olhar_frente")

    assert acao is not None
    assert falado == ""


def test_toda_acao_de_cabeca_tem_pan_e_tilt_definidos():
    """Meia posicao nao existe: mandar so o pan deixaria o tilt em qualquer
    lugar que a ultima ordem tiver deixado, e "olha pra esquerda" daria
    resultados diferentes conforme o passado."""
    for acao in ACOES.values():
        if acao.comando:
            continue
        assert acao.pan is not None, acao.nome
        assert acao.tilt is not None, acao.nome


def test_o_prompt_lista_o_catalogo_inteiro():
    """Acao que existe em codigo e nao aparece no prompt e inalcancavel: o
    modelo nao pode escolher o que nao sabe que existe. Este teste e o que
    impede o catalogo e o prompt de envelhecerem em ritmos diferentes."""
    bloco = bloco_para_prompt()

    for nome in ACOES:
        assert nome in bloco, f"{nome} nao aparece no prompt"
