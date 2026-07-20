"""Grounding: transforma o que o robô SABE num bloco de texto para a IA.

POR QUE ISTO EXISTE
-------------------
Medido em 2026-07-19 com prompts reais: perguntado "a Ana passou por
aqui hoje?" sem nenhum dado sobre a Ana, o gemma3:1b respondeu "Sim, vi!"
e o llama3.2:3b inventou um registro de visita "às 14h30". Modelo nenhum
sabe quem passou na sala - sem informação, todos chutam.

Trocar de modelo não conserta isso. O que conserta é entregar os fatos e,
principalmente, DIZER O QUE NÃO SE SABE. Um campo ausente é um convite à
invenção; "não há registro" fecha a porta.

Este módulo não fala com a IA - só formata. Assim dá para testar o texto
gerado sem depender do Ollama.
"""
from __future__ import annotations

from typing import Any

# Frase usada para marcar ausência de dado. Fica igual em todo lugar de
# propósito: o modelo aprende o padrão dentro do próprio prompt.
SEM_DADO = "não sei (sem registro)"


def _linha(rotulo: str, valor: Any, sufixo: str = "") -> str:
    """Uma linha de fato. Valor None vira explicitamente 'não sei'."""
    if valor is None or valor == "":
        return f"- {rotulo}: {SEM_DADO}"
    if isinstance(valor, bool):
        valor = "sim" if valor else "não"
    return f"- {rotulo}: {valor}{sufixo}"


def bloco_corpo(retrato: dict[str, Any] | None) -> str:
    """O que o robô sente do próprio corpo (vem do RetratoDoMundo)."""
    r = retrato or {}
    linhas = [
        _linha("obstáculo à frente", r.get("obstaculo_frente_cm"), " cm"),
        _linha("inclinação", r.get("inclinacao_graus"), " graus"),
        _linha("bateria", r.get("bateria_nivel")),
        _linha("estado do corpo", r.get("estado_hardware")),
    ]
    if r.get("telemetria_viva") is False:
        linhas.append(
            "- ATENÇÃO: perdi contato com meu corpo - os dados acima podem "
            "estar desatualizados"
        )
    return "O QUE EU SINTO AGORA:\n" + "\n".join(linhas)


def bloco_pessoas(retrato: dict[str, Any] | None, familia: list[str] | None) -> str:
    """Quem o robô está vendo AGORA e quem ele conhece."""
    r = retrato or {}
    linhas = []

    if r.get("pessoa_presente"):
        nome = r.get("pessoa_nome")
        if nome:
            linhas.append(f"- neste exato momento estou vendo: {nome}")
        else:
            linhas.append("- neste exato momento estou vendo: alguém que NÃO reconheço")
    else:
        # "neste exato momento" e nao so "agora": medido em 2026-07-19, o
        # gemma3:1b lia "nao estou vendo ninguem agora" e respondia "nao vi"
        # a perguntas sobre o DIA INTEIRO, ignorando os registros logo
        # abaixo. Separar os dois tempos com clareza no proprio texto e o
        # que faz um modelo pequeno nao confundir agora com hoje.
        linhas.append(
            "- neste exato momento não estou vendo ninguém "
            "(isso NÃO diz nada sobre o resto do dia - para isso use os "
            "registros abaixo)"
        )

    if familia:
        linhas.append(f"- pessoas que eu conheço: {', '.join(familia)}")

    return "QUEM EU VEJO:\n" + "\n".join(linhas)


def bloco_observacoes(observacoes: list[dict[str, Any]] | None) -> str:
    """Registros do que o robô realmente observou (vindos da memória).

    Lista vazia é informação, não ausência de informação: significa "olhei e
    não vi nada". Por isso ela é dita em voz alta em vez de omitida.
    """
    if not observacoes:
        return (
            "TUDO O QUE EU VI HOJE (meu diário completo do dia):\n"
            "- o diário está VAZIO: não vi ninguém e nada aconteceu hoje\n"
            "- se perguntarem sobre algo de hoje, eu realmente não vi e devo "
            "dizer isso"
        )

    linhas = [
        f"- às {o.get('quando', 'hora desconhecida')} eu vi: {o.get('o_que', '?')}"
        for o in observacoes
    ]
    return (
        "TUDO O QUE EU VI HOJE (meu diário completo do dia):\n"
        + "\n".join(linhas)
        + "\n- estes são fatos que EU VI. Se a pergunta for sobre alguém "
        "desta lista, responda que SIM, eu vi, e diga a hora."
    )


REGRA_ANTI_INVENCAO = """REGRA MAIS IMPORTANTE - vale nos DOIS sentidos:

1. Se os fatos acima RESPONDEM a pergunta, use-os e responda com confiança.
   Se o meu diário diz que eu vi alguém hoje, então eu VI - responda que
   sim e diga a hora. Negar o que está escrito no diário é tão errado
   quanto inventar.

2. Se os fatos acima NÃO cobrem a pergunta, diga que não sabe ou não viu,
   com naturalidade. Nunca invente uma observação, um horário ou uma
   visita que não esteja escrita ali.

Resumindo: o diário é a verdade. Não acrescente nada a ele, e não negue
nada que esteja nele."""


def montar_contexto(
    retrato: dict[str, Any] | None = None,
    familia: list[str] | None = None,
    observacoes: list[dict[str, Any]] | None = None,
    conversas_recentes: list[dict[str, Any]] | None = None,
) -> str:
    """Junta tudo num bloco pronto para colar no prompt de sistema."""
    partes = [
        bloco_corpo(retrato),
        bloco_pessoas(retrato, familia),
        bloco_observacoes(observacoes),
    ]

    if conversas_recentes:
        historico = "\n".join(
            f"- {c.get('papel', '?')}: {c.get('texto', '')}"
            for c in conversas_recentes[-5:]
        )
        partes.append("CONVERSA RECENTE:\n" + historico)

    partes.append(REGRA_ANTI_INVENCAO)
    return "\n\n".join(partes)
