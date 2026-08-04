"""O catalogo de acoes faladas e a leitura do marcador.

O que estes testes protegem e a fronteira entre TEXTO e COMANDO. Ela e fina de
proposito - uma linha no comeco da resposta - e do lado errado dela estao os
dois modos de falha que importam: o robo falar "sustenido acao dois pontos" em
voz alta, e o robo obedecer a uma frase que so FALAVA sobre um comando.
"""
from orion.mission.acoes_faladas import (
    ACOES,
    MARCADOR,
    bloco_para_prompt,
    extrair,
    partir_recado,
)


def test_marcador_no_comeco_vira_acao_e_some_do_texto():
    acao, _argumento, falado = extrair("#acao:olhar_direita\nOlhando pra direita.")

    assert acao is not None
    assert acao.nome == "olhar_direita"
    assert falado == "Olhando pra direita."


def test_marcador_no_MEIO_do_texto_e_texto_e_nao_comando():
    """`\\A` e nao `^`: se o robo esta EXPLICANDO como funciona, ou lendo em voz
    alta o que alguem escreveu, isso nao pode mover servo nenhum. Comando so no
    comeco - e a mesma razao por que o `parar` exige `\\b`."""
    texto = "Funciona assim: eu escrevo #acao:olhar_direita e a cabeca vira."

    acao, _argumento, falado = extrair(texto)

    assert acao is None
    assert falado == texto


def test_conversa_normal_passa_intacta():
    acao, _argumento, falado = extrair("Tudo bem sim, e voce?")

    assert acao is None
    assert falado == "Tudo bem sim, e voce?"


def test_acao_desconhecida_nao_vira_chute_e_o_marcador_some():
    """Regra 6 do contrato, aplicada aqui: fora do catalogo = nada acontece.
    Mas o marcador sai do texto assim mesmo - alucinacao do modelo nao pode
    virar ruido no alto-falante."""
    acao, _argumento, falado = extrair("#acao:teletransportar\nJa fui.")

    assert acao is None
    assert MARCADOR not in falado
    assert falado == "Ja fui."


def test_so_o_marcador_sem_frase_deixa_o_texto_vazio():
    """Quem chama precisa distinguir "nao ha o que falar" de "falar isto" -
    senao o sintetizador carrega o Piper para produzir silencio."""
    acao, _argumento, falado = extrair("#acao:olhar_frente")

    assert acao is not None
    assert falado == ""


def test_toda_acao_de_cabeca_tem_pan_e_tilt_definidos():
    """Meia posicao nao existe: mandar so o pan deixaria o tilt em qualquer
    lugar que a ultima ordem tiver deixado, e "olha pra esquerda" daria
    resultados diferentes conforme o passado."""
    for acao in ACOES.values():
        if acao.entrega != "cabeca":
            continue
        assert acao.pan is not None, acao.nome
        assert acao.tilt is not None, acao.nome


def test_recado_sem_argumento_nao_vira_acao():
    """PROMETER SEM PODER CUMPRIR e o defeito original, com mais codigo em
    volta: um `deixar_recado` vazio guardaria um recado sem destinatario e sem
    mensagem, e o robo teria dito "combinado" assim mesmo."""
    acao, argumento, falado = extrair("#acao:deixar_recado\nCombinado!")

    assert acao is None
    assert argumento == ""
    assert falado == "Combinado!"


def test_recado_com_argumento_carrega_pessoa_e_mensagem():
    acao, argumento, falado = extrair(
        "#acao:deixar_recado Mara | o Joao quer falar com voce\nCombinado."
    )

    assert acao is not None and acao.nome == "deixar_recado"
    assert partir_recado(argumento) == ("Mara", "o Joao quer falar com voce")
    assert falado == "Combinado."


def test_argumento_sem_separador_nao_vira_recado():
    """Adivinhar erraria dos dois jeitos: tudo como nome guardaria um recado
    para uma pessoa chamada "mara quero falar com ela"; tudo como mensagem
    guardaria um recado para ninguem."""
    assert partir_recado("Mara quero falar com ela") == ("", "")
    assert partir_recado("| so a mensagem") == ("", "")
    assert partir_recado("so o nome |") == ("", "")


def test_acao_sem_argumento_ignora_lixo_na_linha():
    """O modelo as vezes escreve `#acao:olhar_esquerda agora` - a acao vale, e
    o "agora" nao pode vazar para a fala."""
    acao, _argumento, falado = extrair("#acao:olhar_esquerda agora\nOlhando.")

    assert acao is not None and acao.nome == "olhar_esquerda"
    assert falado == "Olhando."


def test_o_prompt_lista_o_catalogo_inteiro():
    """Acao que existe em codigo e nao aparece no prompt e inalcancavel: o
    modelo nao pode escolher o que nao sabe que existe. Este teste e o que
    impede o catalogo e o prompt de envelhecerem em ritmos diferentes."""
    bloco = bloco_para_prompt()

    for nome in ACOES:
        assert nome in bloco, f"{nome} nao aparece no prompt"
