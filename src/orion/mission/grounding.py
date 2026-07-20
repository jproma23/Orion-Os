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
            linhas.append(f"- estou vendo agora: {nome}")
        else:
            linhas.append("- estou vendo agora: alguém que NÃO reconheço")
    else:
        linhas.append("- não estou vendo ninguém agora")

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
            "MEUS REGISTROS DE HOJE:\n"
            "- não tenho NENHUM registro de pessoas ou eventos hoje\n"
            "- se perguntarem sobre algo que aconteceu, eu não vi e devo dizer isso"
        )

    linhas = [
        f"- {o.get('quando', 'hora desconhecida')}: {o.get('o_que', '?')}"
        for o in observacoes
    ]
    return "MEUS REGISTROS DE HOJE:\n" + "\n".join(linhas)


REGRA_ANTI_INVENCAO = """REGRA MAIS IMPORTANTE:
Você só pode afirmar o que está escrito nos fatos acima. Se perguntarem
algo que os fatos não cobrem, responda que não sabe ou não viu - com
naturalidade, sem se desculpar demais. NUNCA invente uma observação, um
horário ou um registro. Dizer "não sei" é a resposta certa e esperada;
inventar é o erro mais grave que você pode cometer."""


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
