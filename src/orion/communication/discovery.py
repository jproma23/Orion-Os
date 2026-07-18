"""Descoberta de dispositivos (Cap 14 secao 8).

No boot, cada enlace faz um WHO_ARE_YOU e confere a versao de protocolo do
outro lado antes de considera-lo utilizavel. `ComunicacaoService` ja
responde automaticamente a um WHO_ARE_YOU recebido (ver service.py); esta
funcao e o lado de quem pergunta.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from orion.communication.protocol import VERSAO_PROTOCOLO
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus, Prioridade

logger = logging.getLogger("orion.communication.discovery")


class ErroVersaoIncompativel(Exception):
    """A versao de protocolo do peer descoberto nao bate com a nossa (Cap 14 s.8)."""


@dataclass
class InformacaoDescoberta:
    nome: str
    versao_modulo: str
    versao_protocolo: str


async def descobrir(
    servico: ComunicacaoService,
    destino: str,
    event_bus: EventBus,
    timeout_s: float = 2.0,
) -> InformacaoDescoberta:
    """Envia WHO_ARE_YOU a `destino` e valida a versao de protocolo.

    Publica comm.protocol_mismatch e levanta ErroVersaoIncompativel se as
    versoes nao baterem - quem chama decide colocar o modulo em modo
    degradado em vez do Communication Core decidir isso sozinho.
    """
    resposta = await servico.request(destino, {"comando": "WHO_ARE_YOU"}, timeout_s=timeout_s)
    info = InformacaoDescoberta(
        nome=resposta.payload["nome"],
        versao_modulo=resposta.payload["versao_modulo"],
        versao_protocolo=resposta.payload["versao_protocolo"],
    )

    if info.versao_protocolo != VERSAO_PROTOCOLO:
        await event_bus.publish(
            "comm.protocol_mismatch",
            {
                "destino": destino,
                "versao_recebida": info.versao_protocolo,
                "versao_esperada": VERSAO_PROTOCOLO,
            },
            prioridade=Prioridade.ALTA,
        )
        raise ErroVersaoIncompativel(
            f"Versao de protocolo incompativel com '{destino}': "
            f"recebido {info.versao_protocolo}, esperado {VERSAO_PROTOCOLO}"
        )

    logger.info(
        "Descoberta OK: destino=%s nome=%s versao_modulo=%s",
        destino,
        info.nome,
        info.versao_modulo,
    )
    return info
