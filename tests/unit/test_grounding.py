"""Testes do grounding (src/orion/mission/grounding.py).

O que estes testes protegem: o bloco de contexto tem que dizer EM VOZ ALTA
o que o robô não sabe. Medido em 2026-07-19, o silêncio sobre um fato fazia
os modelos inventarem (gemma3:1b e llama3.2:3b afirmaram ter visto uma
pessoa que nunca viram); com o "não sei" explícito, os quatro modelos
testados passaram a recusar corretamente.
"""
from __future__ import annotations

from orion.mission.grounding import (
    REGRA_ANTI_INVENCAO,
    SEM_DADO,
    bloco_corpo,
    bloco_observacoes,
    bloco_pessoas,
    bloco_visao_agora,
    montar_contexto,
)


def test_campo_ausente_vira_nao_sei_explicito() -> None:
    """Nada de campo omitido: ausência tem que estar escrita.

    A frente saiu da contagem em 2026-07-29: ela ganhou texto próprio
    (`_linha_frente`), porque "não sei a distância" precisa soar como convite a
    olhar, e não como a parede que o rótulo antigo sugeria.
    """
    texto = bloco_corpo({})
    assert texto.count(SEM_DADO) == 2  # bateria, estado
    # os lados do LiDAR (desde 2026-11-01) dizem "não sei" com o motivo
    assert texto.count("o LiDAR não mediu nada nesse lado") == 3
    assert "NÃO SEI a distância" in texto


def test_corpo_com_dados_mostra_valores() -> None:
    texto = bloco_corpo(
        {
            "obstaculo_frente_cm": 34.0,
            "obstaculo_tras_cm": 21.0,
            "bateria_nivel": "ok",
            "estado_hardware": "IDLE",
            "telemetria_viva": True,
        }
    )
    # 34 cm com limiar de 25 e caminho livre, e o texto tem que dizer isso -
    # ate 2026-07-29 saia como "obstáculo à frente: 34.0 cm" e a IA parava.
    assert "espaço CURTO, 34 cm" in texto  # 25 < 34 < 60: da para andar pouco
    assert "atrás de mim: OBSTÁCULO a 21 cm" in texto  # 21 < 25: perto demais
    assert SEM_DADO not in texto


def test_corpo_avisa_quando_perdeu_contato() -> None:
    """Telemetria morta tem que aparecer como aviso, não sumir em silêncio."""
    texto = bloco_corpo({"telemetria_viva": False})
    assert "perdi contato" in texto


def test_pessoa_desconhecida_e_dita_como_desconhecida() -> None:
    texto = bloco_pessoas({"pessoa_presente": True, "pessoa_nome": None}, None)
    assert "NÃO reconheço" in texto


def test_pessoa_conhecida_aparece_pelo_nome() -> None:
    texto = bloco_pessoas(
        {"pessoa_presente": True, "pessoa_nome": "João Paulo"}, ["João Paulo", "Ana"]
    )
    assert "estou vendo: João Paulo" in texto
    assert "Ana" in texto  # lista de conhecidos


def test_ninguem_presente_e_afirmado() -> None:
    assert "não estou vendo ninguém" in bloco_pessoas({"pessoa_presente": False}, None)


def test_sem_observacoes_diz_que_nao_viu_nada() -> None:
    """Lista vazia é informação ('olhei e não vi'), não ausência dela.

    Este é o caso exato que fazia os modelos inventarem visitas.
    """
    texto = bloco_observacoes([])
    assert "VAZIO" in texto
    assert "devo dizer isso" in texto


def test_observacoes_viram_linhas_datadas() -> None:
    texto = bloco_observacoes([{"quando": "14:30", "o_que": "Bruno chegou"}])
    assert "14:30" in texto and "Bruno chegou" in texto
    # o bloco tem que INSTRUIR a usar o registro, nao so lista-lo: sem
    # isso o gemma3:1b negava ter visto alguem que estava no diario
    # (medido 2026-07-19).
    assert "responda que SIM" in texto


def test_contexto_completo_sempre_traz_a_regra_anti_invencao() -> None:
    texto = montar_contexto()
    assert REGRA_ANTI_INVENCAO in texto
    # A proibicao de inventar OBSERVACAO continua - so mudou a redacao em
    # 2026-07-29 (era "Nunca invente"), quando a regra passou a dizer que vale
    # para FATOS da casa e nao para conversa. O teste passou a checar a
    # intencao, nao a frase: prender a redacao literal fazia o teste quebrar
    # por melhoria de texto, o que treina a gente a "consertar" o teste sem ler.
    assert "se não está escrito, eu não vi" in texto
    # a regra tem que valer nos DOIS sentidos - so proibir inventar fazia
    # o modelo recusar ate o que estava escrito no diario.
    assert "Negar o que está escrito no diário" in texto


def test_contexto_inclui_conversa_recente() -> None:
    texto = montar_contexto(
        conversas_recentes=[{"papel": "usuario", "texto": "bom dia"}]
    )
    assert "bom dia" in texto


def test_contexto_limita_historico_a_cinco_falas() -> None:
    conversas = [{"papel": "usuario", "texto": f"fala {i}"} for i in range(10)]
    texto = montar_contexto(conversas_recentes=conversas)
    assert "fala 9" in texto
    assert "fala 4" not in texto  # só as 5 últimas


# ---------- a visão atual entra no contexto (2026-08-09) ----------


def test_bloco_visao_agora_marca_o_presente_e_rejeita_o_diario() -> None:
    bloco = bloco_visao_agora("Na minha frente agora estou vendo um controle remoto.")
    assert "Na minha frente agora estou vendo um controle remoto." in bloco
    assert "O QUE ESTOU VENDO AGORA" in bloco
    assert "PRESENTE" in bloco
    assert "passado" in bloco and "diário" in bloco


def test_bloco_visao_agora_sem_cena_fica_vazio() -> None:
    assert bloco_visao_agora(None) == ""
    assert bloco_visao_agora("") == ""


def test_contexto_inclui_a_visao_agora_quando_ha_cena() -> None:
    texto = montar_contexto(visao_agora="Na minha frente agora estou vendo um gato.")
    assert "estou vendo um gato" in texto
    assert "O QUE ESTOU VENDO AGORA" in texto


# ---------- a frente descrita pelo que ELA E (2026-07-29) ----------


def test_caminho_livre_nao_e_anunciado_como_obstaculo():
    """REGRESSAO medida em producao. A linha era
    `- obstáculo à frente: 199.3 cm`, e a IA lia "ha um obstaculo" - o numero
    virava detalhe. Com a frente desimpedida a 2 metros ela decidiu repetidas
    vezes `parar`, justificando com "evitar colisoes com o obstaculo a frente".
    """
    texto = bloco_corpo({"obstaculo_frente_cm": 199.3}, limiar_obstaculo_cm=25.0)

    assert "CAMINHO LIVRE" in texto
    assert "199" in texto
    assert "25" in texto, "o limiar precisa aparecer, senao o numero nao tem escala"
    # a palavra "obstáculo" nao pode descrever 2 metros de espaco vazio
    linha_frente = [ln for ln in texto.splitlines() if "à minha frente" in ln][0]
    assert "OBSTÁCULO" not in linha_frente


def test_obstaculo_de_verdade_continua_sendo_anunciado():
    """Contraprova: o conserto nao pode calar o aviso quando ha coisa perto."""
    texto = bloco_corpo({"obstaculo_frente_cm": 12.0}, limiar_obstaculo_cm=25.0)

    linha_frente = [ln for ln in texto.splitlines() if "à minha frente" in ln][0]
    assert "OBSTÁCULO" in linha_frente
    assert "12" in linha_frente


def test_frente_sem_medida_e_duvida_e_nao_perigo():
    """`sem_eco` chega aqui como None. "Nao sei" tem que soar como convite a
    olhar, nao como parede - senao o robo trata o proprio ponto cego como
    obstaculo e nunca sai do lugar."""
    texto = bloco_corpo({"obstaculo_frente_cm": None}, limiar_obstaculo_cm=25.0)

    linha_frente = [ln for ln in texto.splitlines() if "à minha frente" in ln][0]
    assert "NÃO SEI" in linha_frente
    assert "OBSTÁCULO" not in linha_frente


def test_espaco_atras_usa_o_nome_certo_do_campo():
    """O grounding pedia `distancia_tras_cm`; o RetratoDoMundo entrega
    `obstaculo_tras_cm`. Nomes diferentes = "nao sei" para sempre."""
    texto = bloco_corpo({"obstaculo_tras_cm": 94.0})

    assert "94" in texto
    assert "atrás de mim: não sei" not in texto


def test_contexto_convida_a_explorar():
    """REGRESSAO de comportamento (2026-07-29): com a frente livre por 2 metros
    a IA escolhia `parar` e `observar_ambiente` ciclo apos ciclo. Metade da
    causa era dado errado (ja corrigido); a outra metade e que ninguem nunca
    disse a ela que explorar era DESEJAVEL - so que era permitido. Uma lista de
    opcoes nao e um convite.
    """
    texto = montar_contexto(retrato={"obstaculo_frente_cm": 199.0})

    assert "EXPLORAR" in texto
    assert "FERRAMENTAS" in texto
    # e o limite continua sendo de codigo, nao de texto - o prompt nao promete
    # liberdade que o contrato nao da. (Trecho curto de proposito: o texto e
    # quebrado em varias linhas, entao frase longa nao casa por substring.)
    assert "não preciso ter medo" in texto


def test_disposicao_vem_antes_dos_fatos():
    """Modelo pequeno pesa mais o comeco do prompt, e a pergunta feita a ele e
    "o que eu faco agora?" - saber quem e vem antes de saber os numeros."""
    texto = montar_contexto(retrato={"obstaculo_frente_cm": 199.0})

    assert texto.index("QUEM EU SOU") < texto.index("O QUE EU SINTO AGORA")


# ----- a regra vale para FATOS, nao para conversa (2026-07-29) -----
#
# Reclamacao do dono ao vivo: "essas respostas sao pessimas, kd a IA". Ele
# perguntou "como e que voce esta hoje?" - transcrito CERTO - e ouviu
# "Nao sei como estou hoje. Nao tenho registros sobre isso.". A IA nao estava
# incapaz: estava amordacada. A regra dizia "o diario e a verdade, nao
# acrescente nada" e o modelo generalizava para TUDO.


def test_regra_separa_fato_da_casa_de_conversa() -> None:
    """O texto tem que dizer explicitamente que a restricao e sobre FATOS
    desta casa - sem isso o modelo aplica a tudo, inclusive a "bom dia"."""
    assert "FATOS" in REGRA_ANTI_INVENCAO or "FATO" in REGRA_ANTI_INVENCAO
    baixo = REGRA_ANTI_INVENCAO.lower()
    # o que ele PODE responder sem registro
    assert "como eu estou" in baixo or "como você está" in baixo
    assert "conversa" in baixo
    # e o erro concreto, nomeado, para o modelo nao repeti-lo
    assert "não tenho registros" in baixo


def test_regra_continua_proibindo_inventar_observacao() -> None:
    """O afrouxamento NAO pode abrir a porta para inventar o que viu - era o
    motivo de a regra existir (os modelos inventavam visitas, medido em
    2026-07-19)."""
    baixo = REGRA_ANTI_INVENCAO.lower()
    assert "nunca invento" in baixo or "não invento" in baixo or "inventar" in baixo
    assert "diário" in baixo
    assert "se não está escrito, eu não vi" in baixo


def test_regra_segue_no_contexto_completo() -> None:
    texto = montar_contexto(retrato={"obstaculo_frente_cm": 100.0})
    assert REGRA_ANTI_INVENCAO in texto


def test_retrato_fala_dos_sentidos_de_verdade():
    """Queixa do dono (2026-11-01): ele nao usava bussola, LiDAR nem visao."""
    texto = bloco_corpo(
        {
            "obstaculo_frente_cm": 80.0,
            "obstaculo_esquerda_cm": 12.0,
            "obstaculo_direita_cm": 150.0,
            "obstaculo_tras_cm": None,
            "rumo_graus": 236.0,
            "posicao_x_m": 0.5,
            "posicao_y_m": -0.25,
            "objetos_vistos": ("chair (à esquerda)", "tv (à frente)"),
        },
        limiar_obstaculo_cm=25.0,
    )
    assert "à minha esquerda: OBSTÁCULO a 12 cm" in texto
    assert "à minha direita: livre por 150 cm" in texto
    assert "236° (sudoeste" in texto
    assert "x=0.50 m, y=-0.25 m" in texto
    assert "chair (à esquerda), tv (à frente)" in texto
    assert "45 cm do chão" in texto
