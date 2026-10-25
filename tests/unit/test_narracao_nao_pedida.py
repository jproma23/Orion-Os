"""Testes do "agir sem pedir pode; anunciar sem pedir, nao" (2026-08-09).

Nasceram de uma queixa do dono, com os numeros do chat de 08 e 09/08 na mao:
em 26 de 123 respostas o robo anunciou um movimento no meio de outro assunto.

> *"ele pode se mexer sem ninguem pedir, ele e autonomo; o q precisa e nao
> ficar falando coisas q nao tem nada a ver"*

Entao NENHUM teste aqui exige que a acao seja barrada - ela roda sempre. O
que estes testes protegem e a FALA: que a narracao suma quando ninguem pediu,
e que ela CONTINUE saindo quando alguem pediu (robo que obedece calado parece
robo que travou).

Todas as frases sao reais, tiradas da tabela `conversas` nas datas citadas.
"""
import pytest

from orion.mission.acoes_faladas import ACOES
from orion.mission.comando_local import pediu_de_fato, pediu_para_guardar
from orion.mission.mission_planner import _sem_narracao

# --- Ninguem pediu: a narracao nao pode sair -------------------------------

@pytest.mark.parametrize(
    "acao,fala",
    [
        # As 12 frases que sequestraram a fala em 08 e 09/08.
        ("olhar_frente", "Você conhece a Bíblia?"),
        ("olhar_frente", "Me explica aí alguma coisa"),
        ("olhar_frente", "Volta no assunto novamente"),
        ("olhar_frente", "Ok Mas eu estou com papo Chega"),
        ("olhar_frente", "Jarvis"),
        ("olhar_cima", "Você sabe que é o livre arbítrio?"),
        ("olhar_direita", "Eu quero falar sobre outra coisa"),
        ("olhar_direita", "Jarvis Jarvis Jarvis Jarvis"),
        ("varrer_a_frente", "Me explica aí alguma coisa"),
        ("varrer_a_frente", "Vamos lá, vamos lá"),
        ("varrer_a_frente", "Você consegue passar um relatório de saúde sua?"),
        # O pior deles: o robo ANDOU por causa disto. Andar segue permitido;
        # anunciar nao.
        ("andar_para_frente", "Bora robô! Licas no motor! Deixe preguiça!"),
    ],
)
def test_ninguem_pediu_entao_nao_anuncia(acao, fala):
    assert not pediu_de_fato(acao, fala)


# --- Pediu: a confirmacao TEM que sair -------------------------------------

@pytest.mark.parametrize(
    "acao,fala",
    [
        # Pedidos reais do dono, das mesmas conversas.
        ("olhar_frente", "aqui, olha para cá, e me olhe para mim"),
        ("virar_esquerda", "Você gira a esquerda"),
        ("virar_esquerda", "Isso, faça isso, gire no seu próprio eixo pra esquerda"),
        ("andar_para_frente", "15 minutos andando reto, aumenta a velocidade"),
        ("acender_luz", "Acenda a luz aí, por favor"),
        # O imperativo entrou nos gatilhos junto com este portao: sem ele,
        # "olhe" e "gire" - como o dono fala - ficavam de fora.
        ("olhar_esquerda", "olhe pra esquerda"),
        ("olhar_baixo", "abaixe a cabeça"),
        ("varrer_a_frente", "varre a frente aí"),
    ],
)
def test_quem_pediu_ouve_a_confirmacao(acao, fala):
    assert pediu_de_fato(acao, fala)


def test_acao_sem_gatilho_sempre_confirma():
    """Recado e conhecimento nao sao narracao de movimento.

    A confirmacao do recado tem que sair sempre: prometer calado foi o defeito
    que criou o modulo de recados.
    """
    assert pediu_de_fato("deixar_recado", "conversa qualquer sobre outra coisa")


# --- O corte da narracao dentro da fala ------------------------------------

@pytest.mark.parametrize(
    "acao,antes,depois",
    [
        # Falas reais de 09/08, com a narracao colada na frente.
        (
            "olhar_frente",
            "Olhando pra frente. Olha só, agora virei parte da história!",
            "Olha só, agora virei parte da história!",
        ),
        (
            "olhar_frente",
            "Endireitando a cabeça. Manda o que precisar!",
            "Manda o que precisar!",
        ),
        (
            "varrer_a_frente",
            "Varrendo a frente aqui para ver o movimento. O Brasil já acolheu quem?",
            "O Brasil já acolheu quem?",
        ),
        # Duas narracoes encadeadas: as duas somem, o conteudo fica.
        (
            "varrer_a_frente",
            "Varrendo a frente. Deixando o radar ligado aqui na frente. O Brasil já acolheu quem?",
            "O Brasil já acolheu quem?",
        ),
    ],
)
def test_narracao_sai_e_o_conteudo_fica(acao, antes, depois):
    assert _sem_narracao(antes, ACOES[acao]) == depois


@pytest.mark.parametrize(
    "texto",
    [
        # ESTE derrubou o primeiro desenho, que cortava por tamanho: sete
        # palavras, com "olha" e "virei" dentro, e mesmo assim e a resposta.
        "Olha só, agora virei parte da história!",
        "Olha só, agora virei oficialmente parte da história e da imigração!",
    ],
)
def test_nao_come_a_resposta_de_verdade(texto):
    """Falar do mesmo assunto da acao nao faz uma frase virar narracao.

    O que faz e a forma do verbo: narracao abre em gerundio ("Olhando"),
    resposta nao.
    """
    assert _sem_narracao(texto, ACOES["olhar_frente"]) == texto


def test_frase_sem_relacao_com_a_acao_nao_e_tocada():
    texto = "A fotossíntese é como a planta faz o próprio alimento."
    assert _sem_narracao(texto, ACOES["varrer_a_frente"]) == texto


# --- Gravar sem mandar ------------------------------------------------------

@pytest.mark.parametrize(
    "fala",
    [
        # As TRES notas que o robo gravou sozinho em 09/08, todas de
        # transcricao errada, todas ainda no vault quando isto foi escrito.
        "Sente-se de comunicar, DCP, Pará TTP, Suquete, o Suquete Zubin",
        "Os computadores e celulares conectados na rede são chamados de OSPVE",
        "Eu me chamo João Palmo Sou ele de Sistema de Sistema 3",
    ],
)
def test_nao_grava_o_que_ninguem_mandou_gravar(fala):
    assert not pediu_para_guardar(fala)


@pytest.mark.parametrize(
    "fala",
    [
        "guarda isso aí",
        "anota que a Mara chega às 8",
        "lembra disso: o portão trava quando chove",
        "memoriza esse número",
    ],
)
def test_grava_quando_mandam(fala):
    assert pediu_para_guardar(fala)


@pytest.mark.parametrize("fala", ["não anota isso", "nao guarda isso não"])
def test_negacao_anula_o_pedido_de_guardar(fala):
    assert not pediu_para_guardar(fala)
