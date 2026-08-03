"""Testes do planejador GOAP e do modelo de acoes.

O que estes testes protegem, em ordem de importancia:

1. Que o plano de DOIS PASSOS continua saindo. E a razao inteira do GOAP
   existir - se ele parar de sair, o robo voltou a ser o de escolha unica com
   mais codigo em volta.
2. Que "nao da" continua sendo uma resposta. Um planejador que sempre devolve
   algum plano e um que aprendeu a chutar.
3. Que as travas de import (`_conferir_cobertura`, `conferir_modelo`) pegam de
   verdade o erro que existem para pegar.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from motion_core.behavior import contrato, goap, modelo_goap
from motion_core.behavior.goap import (
    PREDICADOS,
    Objetivo,
    conferir_modelo,
    estado_de,
    planejar,
)

MUNDO_BOM = {
    "corpo_vivo": True,
    "frente_conhecida": False,
    "frente_livre": False,
    "pessoa_visivel": False,
    "pessoa_conhecida": False,
    "nivelado": True,
    "bateria_ok": True,
    "sem_impacto": True,
}


def _planejar(estado, objetivo):
    return planejar(estado, modelo_goap.OBJETIVOS_POR_NOME[objetivo], modelo_goap.ACOES_PLANEJAVEIS)


def _nomes(plano):
    return [acao.nome for acao in plano]


# --------------------------------------------------------------------------
# O que o GOAP veio fazer
# --------------------------------------------------------------------------


def test_plano_de_dois_passos_para_andar_estando_cego():
    """A razao de existir do GOAP, num teste so.

    O robo nao sabe o que ha na frente e quer caminho livre. Nenhuma acao
    isolada resolve: `virar_esquerda` exige saber o que ha na frente, e
    `observar_ambiente` sozinho nao libera caminho. So a SEQUENCIA resolve -
    e e ela que o sistema de acao unica nunca conseguiu montar.
    """
    plano = _planejar(MUNDO_BOM, "poder_seguir_em_frente")
    assert _nomes(plano) == ["observar_ambiente", "virar_esquerda"]


def test_objetivo_ja_satisfeito_devolve_plano_vazio():
    """Lista vazia e "nao ha nada a fazer", que e diferente de None ("nao da").

    Quem chama precisa distinguir os dois: vazio significa que o robo ja esta
    onde queria e deve seguir a vida; None significa que o objetivo era
    inalcancavel e outra coisa tem que assumir.
    """
    plano = _planejar({**MUNDO_BOM, "pessoa_visivel": True}, "encontrar_alguem")
    assert plano == []


def test_encontrar_alguem_sai_num_passo():
    plano = _planejar(MUNDO_BOM, "encontrar_alguem")
    assert _nomes(plano) == ["olhar_em_volta"]


def test_plano_e_o_mais_curto_possivel():
    """A heuristica do A* precisa continuar admissivel.

    Se alguem trocar `_distancia` por algo que superestima, o planejador passa
    a devolver planos validos porem mais longos - o robo faria voltas antes de
    chegar no mesmo lugar, e nada apontaria o erro.
    """
    plano = _planejar(MUNDO_BOM, "entender_o_ambiente")
    assert len(plano) == 1


# --------------------------------------------------------------------------
# "Nao da" tem que continuar existindo
# --------------------------------------------------------------------------


def test_sem_corpo_nao_ha_plano():
    """Telemetria morta = o robo nao sabe se tem corpo. Nada de mover.

    Este e o estado real em que o Fofao passou 3h26 em 2026-08-02, com o Mega
    morto: o Motion Core rodando e nenhuma /dev/ttyUSB do outro lado.
    """
    assert _planejar({**MUNDO_BOM, "corpo_vivo": False}, "poder_seguir_em_frente") is None


def test_tombado_nao_planeja_movimento():
    assert _planejar({**MUNDO_BOM, "nivelado": False}, "poder_seguir_em_frente") is None


def test_apos_impacto_nao_planeja_movimento():
    assert _planejar({**MUNDO_BOM, "sem_impacto": False}, "poder_seguir_em_frente") is None


def test_bateria_critica_barra_movimento_mas_nao_observacao():
    """Bateria no fim impede andar, nao impede olhar.

    A distincao importa: cortar tudo faria o robo cego E parado justamente
    quando alguem precisa saber onde ele esta.
    """
    critico = {**MUNDO_BOM, "bateria_ok": False}
    assert _planejar(critico, "poder_seguir_em_frente") is None
    assert _nomes(_planejar(critico, "entender_o_ambiente")) == ["observar_ambiente"]


def test_limite_de_passos_e_respeitado():
    objetivo = Objetivo(nome="impossivel", descricao="", alvo={"pessoa_conhecida": True})
    # Nenhuma acao promete `pessoa_conhecida` - reconhecer alguem e da visao,
    # nao de uma decisao do robo. Entao nao ha plano, por mais fundo que se
    # busque.
    assert planejar(MUNDO_BOM, objetivo, modelo_goap.ACOES_PLANEJAVEIS) is None


# --------------------------------------------------------------------------
# Girar zera o que se sabia da frente
# --------------------------------------------------------------------------


def test_girar_invalida_o_conhecimento_da_frente():
    """Depois de girar, a medida antiga vale para uma direcao que nao e mais a
    do robo. Se este efeito negativo sumir, o planejador passa a montar planos
    sobre dado vencido - e o pior e que eles PARECERIAM corretos."""
    virar = next(a for a in modelo_goap.ACOES_PLANEJAVEIS if a.nome == "virar_esquerda")
    assert virar.efeitos["frente_conhecida"] is False


# --------------------------------------------------------------------------
# Traducao do mundo real
# --------------------------------------------------------------------------


@dataclass
class RetratoFalso:
    telemetria_viva: bool = True
    obstaculo_frente_cm: float | None = None
    inclinacao_graus: float | None = 0.0
    impacto: bool = False
    bateria_nivel: str | None = "OK"
    pessoa_presente: bool = False
    pessoa_conhecida: bool = False


def test_frente_desconhecida_nunca_conta_como_livre():
    """`None` e "nao sei", e nao saber tem que BARRAR o plano de andar.

    E a mesma escolha que o firmware faz com leitura invalida. O sentido do
    erro importa: tratar desconhecido como livre e fail-open, o lado errado
    para falhar.
    """
    estado = estado_de(RetratoFalso(obstaculo_frente_cm=None))
    assert estado["frente_conhecida"] is False
    assert estado["frente_livre"] is False


def test_obstaculo_perto_e_conhecido_mas_nao_livre():
    estado = estado_de(RetratoFalso(obstaculo_frente_cm=30.0))
    assert estado["frente_conhecida"] is True
    assert estado["frente_livre"] is False


def test_bateria_nao_medida_nao_impede_o_robo():
    """O divisor de tensao nunca foi montado, entao `bateria_nivel` e None na
    vida real. Se None virasse "nao ok", o robo ficaria proibido de se mover
    ate alguem soldar dois resistores - e ninguem entenderia por que."""
    assert estado_de(RetratoFalso(bateria_nivel=None))["bateria_ok"] is True
    assert estado_de(RetratoFalso(bateria_nivel="CRITICA"))["bateria_ok"] is False


def test_estado_so_produz_predicados_conhecidos():
    """O tradutor e o modelo tem que falar exatamente o mesmo vocabulario."""
    assert set(estado_de(RetratoFalso())) == set(PREDICADOS)


# --------------------------------------------------------------------------
# As travas de import
# --------------------------------------------------------------------------


def test_todas_as_acoes_do_contrato_estao_modeladas():
    """A trava que paga o preco de o modelo morar fora do contrato.py."""
    assert set(contrato.ACOES) == {a.nome for a in modelo_goap.ACOES_PLANEJAVEIS}


def test_efeito_fora_do_vocabulario_derruba_no_import():
    class AcaoInventada:
        nome = "inventada"
        precondicoes: dict = {}
        efeitos = {"robo_feliz": True}

    with pytest.raises(RuntimeError, match="robo_feliz"):
        conferir_modelo([AcaoInventada()], [])


def test_objetivo_fora_do_vocabulario_derruba_no_import():
    with pytest.raises(RuntimeError, match="tarefa_cumprida"):
        conferir_modelo([], [Objetivo(nome="x", descricao="", alvo={"tarefa_cumprida": True})])


def test_objetivo_fora_da_lista_nunca_vira_chute():
    """Regra 6 do contrato, valendo tambem para objetivos."""
    assert modelo_goap.resolver_objetivo("voar") is None
    assert modelo_goap.resolver_objetivo("") is None
    assert modelo_goap.resolver_objetivo(contrato.ABSTENCAO) is None
    assert modelo_goap.resolver_objetivo("encontrar_alguem") is not None


# ---------------------------------------------------------------------------
# NENHUM OBJETIVO PODE SER DECORATIVO (2026-08-03)
#
# `ficar_em_seguranca` pedia `sem_impacto: True`, e nenhuma acao produz esse
# efeito - impacto e leitura de sensor, nao consequencia de decisao. Varrendo
# os 2^N mundos possiveis, o objetivo nao gerava plano em NENHUM. Com o GOAP
# ligado a IA escolhe entre 4 objetivos: um deles ser mudo custa caro, e o
# nome "ficar em seguranca" e o mais atraente justamente quando as coisas vao
# mal.
# ---------------------------------------------------------------------------


def test_todo_objetivo_produz_plano_em_algum_mundo():
    """Varre todos os mundos possiveis: cada objetivo tem que ser alcancavel
    em pelo menos um deles, senao ele so enfeita a lista oferecida a IA."""
    from itertools import product

    predicados = sorted(goap.PREDICADOS)
    for objetivo in modelo_goap.OBJETIVOS:
        alcancavel = any(
            goap.planejar(
                dict(zip(predicados, combo)), objetivo, modelo_goap.ACOES_PLANEJAVEIS
            )
            for combo in product([False, True], repeat=len(predicados))
        )
        assert alcancavel, (
            f"o objetivo '{objetivo.nome}' nao produz plano em nenhum mundo - "
            f"a IA pode escolhe-lo e o robo nao faz nada, sem erro nenhum"
        )


def test_ficar_em_seguranca_manda_parar():
    """O caso concreto: robo em movimento, objetivo de seguranca -> `parar`."""
    andando = {**MUNDO_BOM, "parado": False}
    plano = _planejar(andando, "ficar_em_seguranca")
    assert _nomes(plano) == ["parar"]


def test_conferir_modelo_recusa_objetivo_decorativo():
    """A trava roda no IMPORT - quem criar outro objetivo mudo descobre no
    boot, nao no quintal."""
    mudo = goap.Objetivo(
        nome="impossivel", descricao="pede o que ninguem faz", alvo={"sem_impacto": True}
    )
    with pytest.raises(RuntimeError, match="decorativo"):
        goap.conferir_modelo(modelo_goap.ACOES_PLANEJAVEIS, [mudo])


def test_heuristica_nunca_superestima():
    """`olhar_em_volta` fecha DOIS predicados de uma vez. Um alvo que peca os
    dois juntos e alcancavel em 1 passo - a heuristica nao pode dizer 2, ou o
    A* deixa de devolver o plano mais curto."""
    composto = {"frente_conhecida": True, "pessoa_visivel": True}
    vazio = dict.fromkeys(goap.PREDICADOS, False)
    assert goap._distancia(vazio, composto) <= 1

    objetivo = goap.Objetivo(nome="ver_tudo", descricao="", alvo=composto)
    plano = goap.planejar(
        {**vazio, "corpo_vivo": True}, objetivo, modelo_goap.ACOES_PLANEJAVEIS
    )
    assert _nomes(plano) == ["olhar_em_volta"], "havia caminho de UM passo"
