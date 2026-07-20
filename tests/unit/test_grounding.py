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
    montar_contexto,
)


def test_campo_ausente_vira_nao_sei_explicito() -> None:
    """Nada de campo omitido: ausência tem que estar escrita."""
    texto = bloco_corpo({})
    assert texto.count(SEM_DADO) == 4  # obstáculo, inclinação, bateria, estado


def test_corpo_com_dados_mostra_valores() -> None:
    texto = bloco_corpo(
        {
            "obstaculo_frente_cm": 34.0,
            "inclinacao_graus": 0.4,
            "bateria_nivel": "ok",
            "estado_hardware": "IDLE",
            "telemetria_viva": True,
        }
    )
    assert "34.0 cm" in texto
    assert "0.4 graus" in texto
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
    assert "Nunca invente" in texto
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
