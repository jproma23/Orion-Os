"""Mission Planner (Cap 7 secao 4) - fluxo de decisao do Mission Core.

1. Receber evento (aqui: texto transcrito de um comando de voz, Cap 9).
2. Consultar contexto e memoria (MemoryClient -> comm.request, Fase 3).
3. Definir prioridade / classificar: comando de movimento, pergunta de
   hora (resposta direta e confiavel, sem depender do LLM "saber" a hora),
   ou pergunta geral (vai para a IA).
4. Consultar IA quando necessario.
5. Criar plano de acao (a resposta a dar / o comando a executar).
6. Enviar missao ao modulo apropriado (`enviar_comando_hardware`, tipicamente
   comm.send ao motion_core, que encaminha ao hardware_core - Cap 14 s.7).
7. Monitorar execucao (o callback injetado e quem aguarda o ACK/erro).
8. Registrar resultado (memory.remember em "conversas").

Classificacao de comando por palavra-chave - um NLU de verdade fica para
uma fase futura; o "minimo" aqui cobre exatamente os casos do criterio de
pronto da Fase 6 (pergunta de hora, comando de lanterna).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Awaitable, Callable

from orion.mission.ai_manager import AiManager
from orion.mission.memory_client import MemoryClient

logger = logging.getLogger("orion.mission.mission_planner")

CallbackComandoHardware = Callable[[str], Awaitable[None]]

_PADROES_COMANDO = (
    # LIGHT_OFF ANTES de LIGHT_ON: "desligue a luz" contem "ligue a luz"
    # como substring - se ON fosse testado primeiro, desligar LIGARIA a
    # lanterna. \w* cobre as conjugacoes (apaga/apague/apagar, desliga/
    # desligue...), que o padrao antigo [ae] nao cobria (bug real: "apague
    # a lanterna" caia na IA, achado no teste de 2026-07-19).
    (re.compile(r"apag\w*.*(lanterna|luz)|deslig\w*.*(lanterna|luz)"), "LIGHT_OFF"),
    (re.compile(r"acend\w*.*(lanterna|luz)|lig\w*.*(lanterna|luz)|luz.*lig"), "LIGHT_ON"),
    (re.compile(r"\bpar[ae]\b|\bparar\b|\bstop\b"), "STOP"),
    (re.compile(r"anda[re]? para frente|va[i]? para frente|siga em frente"), "MOVE_FORWARD"),
    (re.compile(r"vir[ae] (a|para a) esquerda|gir[ae] (a|para a) esquerda"), "TURN_LEFT"),
    (re.compile(r"vir[ae] (a|para a) direita|gir[ae] (a|para a) direita"), "TURN_RIGHT"),
)

_RESPOSTAS_COMANDO = {
    "LIGHT_ON": "Lanterna ligada.",
    "LIGHT_OFF": "Lanterna desligada.",
    "STOP": "Parado.",
    "MOVE_FORWARD": "Indo para frente.",
    "TURN_LEFT": "Virando a esquerda.",
    "TURN_RIGHT": "Virando a direita.",
}


class MissionPlanner:
    def __init__(
        self,
        ai_manager: AiManager,
        enviar_comando_hardware: CallbackComandoHardware | None = None,
        memory_client: MemoryClient | None = None,
    ) -> None:
        self._ai_manager = ai_manager
        self._enviar_comando_hardware = enviar_comando_hardware
        self._memory_client = memory_client

    async def processar(self, texto_usuario: str, pessoa_id: int | None = None) -> str:
        contexto = await self._consultar_contexto(pessoa_id)

        comando_detectado = self._detectar_comando(texto_usuario)
        if comando_detectado is not None:
            resposta = await self._executar_comando(comando_detectado)
        elif self._eh_pergunta_de_hora(texto_usuario):
            resposta = self._responder_hora()
        else:
            resposta = await self._ai_manager.responder(texto_usuario, contexto)

        await self._registrar_interacao(pessoa_id, texto_usuario, resposta)
        return resposta

    async def _consultar_contexto(self, pessoa_id: int | None) -> dict | None:
        if self._memory_client is None:
            return None
        try:
            return await self._memory_client.context(pessoa_id)
        except Exception:
            logger.exception("Falha ao consultar contexto da memoria")
            return None

    @staticmethod
    def _detectar_comando(texto: str) -> str | None:
        texto_normalizado = texto.lower()
        for padrao, comando in _PADROES_COMANDO:
            if padrao.search(texto_normalizado):
                return comando
        return None

    async def _executar_comando(self, comando: str) -> str:
        if self._enviar_comando_hardware is not None:
            try:
                await self._enviar_comando_hardware(comando)
            except Exception:
                logger.exception("Falha ao enviar comando '%s' ao Hardware Core", comando)
                return "Nao consegui executar esse comando agora."
        return _RESPOSTAS_COMANDO.get(comando, "Feito.")

    @staticmethod
    def _eh_pergunta_de_hora(texto: str) -> bool:
        texto_normalizado = texto.lower()
        return "que horas" in texto_normalizado or "horario" in texto_normalizado

    @staticmethod
    def _responder_hora() -> str:
        agora = datetime.now()
        return f"Agora sao {agora.hour} horas e {agora.minute} minutos."

    async def _registrar_interacao(
        self, pessoa_id: int | None, texto_usuario: str, resposta: str
    ) -> None:
        if self._memory_client is None:
            return
        try:
            await self._memory_client.remember(
                "conversas", {"pessoa_id": pessoa_id, "papel": "usuario", "texto": texto_usuario}
            )
            await self._memory_client.remember(
                "conversas", {"pessoa_id": pessoa_id, "papel": "robo", "texto": resposta}
            )
        except Exception:
            logger.exception("Falha ao registrar conversa na memoria")
