"""Ponte da conversa por TEXTO (lado Notebook), ligada em 2026-08-03.

A caixa de texto da aba Conversa (webui, que roda no Pi) manda o que foi
digitado via `comm.request`; esta ponte recebe e entrega ao MESMO
`processar_comando` que uma transcricao do microfone usaria, e devolve a
resposta para aparecer na tela.

**Por que o mesmo ponto de entrada, e nao um caminho proprio.** Um segundo
caminho seria um segundo robo: outro prompt, outro historico, outra memoria -
e o dono ja nomeou empilhar camadas como o defeito recorrente deste projeto.
Entrando por `processar_comando`, o turno digitado passa pelo Mission Planner
igual ao falado, e por isso e gravado em `conversas` (mission_planner grava os
dois lados) e alimenta o extrator de fatos. Escrever "vai na cozinha" tem
exatamente o mesmo efeito que dizer.

Mesmo padrao de `PonteConselho` e `PonteDecisao`: usa ComunicacaoService de
verdade, e nao o Event Bus local puro - evento local nao atravessa a rede
sozinho (achado da vistoria de 2026-07-24).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from orion.communication.protocol import Mensagem
from orion.communication.service import ComunicacaoService
from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("orion.mission.ponte_conversa")

COMANDO_CONVERSA = "mission.conversa"

#: Teto de tamanho do texto recebido. O campo da webui ja limita em 500, mas
#: quem valida e quem RECEBE: o limite do formulario e sugestao, nao garantia -
#: qualquer cliente pode mandar o que quiser neste endereco.
LIMITE_CARACTERES = 500


class PonteConversa:
    """Liga o comando `mission.conversa` recebido via comm.request ao mesmo
    processamento de comando que a voz usa."""

    def __init__(
        self,
        atender: Callable[[str], Awaitable[str]],
        servico: ComunicacaoService,
    ) -> None:
        self._atender = atender
        self._servico = servico

    def registrar(self, event_bus: EventBus) -> None:
        event_bus.subscribe("comm.mensagem.command", self._ao_receber_comando)

    async def _ao_receber_comando(self, evento: Evento) -> None:
        comando = evento.dados.get("payload", {}).get("comando", "")
        if comando != COMANDO_CONVERSA:
            return  # nao e conversa - outra ponte cuida disso

        mensagem_original = Mensagem.from_dict(evento.dados)
        texto = str(mensagem_original.payload.get("texto", "")).strip()

        if not texto:
            await self._servico.responder(
                mensagem_original, {"ok": False, "erro": "mensagem vazia"}
            )
            return
        if len(texto) > LIMITE_CARACTERES:
            await self._servico.responder(
                mensagem_original,
                {"ok": False, "erro": f"mensagem acima de {LIMITE_CARACTERES} caracteres"},
            )
            return

        logger.info("chat: %s", texto)
        try:
            resposta = await self._atender(texto)
        except Exception:
            # Falhar aqui nao pode deixar o Pi esperando ate o timeout: quem
            # digitou esta olhando a tela. Responder "nao deu" e melhor que
            # silencio - mesmo raciocinio de PonteConselho.
            logger.exception("falha ao atender a conversa por texto")
            await self._servico.responder(
                mensagem_original, {"ok": False, "erro": "o Javis nao conseguiu responder"}
            )
            return

        logger.info("chat, resposta: %s", resposta)
        await self._servico.responder(mensagem_original, {"ok": True, "resposta": resposta})
