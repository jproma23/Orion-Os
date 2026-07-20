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

#: "voce viu a Ana hoje?", "viu o Bruno?", "voce viu Ana por aqui?"
#: O nome vai ate o fim ou ate uma palavra de corte (hoje/por aqui/ai...).
_PADRAO_VIU_ALGUEM = re.compile(
    # o artigo exige espaco depois: sem isso o "a" de "alguem" era lido
    # como artigo e sobrava "lguem" como nome.
    r"\bviu\s+(?:(?:a|o|as|os)\s+)?(?P<nome>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,30}?)"
    r"(?:\s+(?:hoje|por\s+aqui|ai|aí|passar|passando|em\s+casa))?\s*[?.!]*$",
    re.IGNORECASE,
)


def _mesmo_nome(nome: str, descricao: str) -> str:
    """A descricao do diario e livre ("vi Joao Paulo"); casa pelo nome.

    Comparacao por prefixo do primeiro nome, sem acento e sem caixa: quem
    pergunta costuma dizer so o primeiro nome ("viu o Joao?") enquanto o
    diario guarda o nome completo cadastrado ("Joao Paulo").
    """
    import unicodedata

    def _normalizar(t: str) -> str:
        t = unicodedata.normalize("NFD", t.lower())
        return "".join(c for c in t if unicodedata.category(c) != "Mn")

    alvo = _normalizar(nome)
    texto = _normalizar(descricao)
    if not alvo:
        return False
    # casa nome inteiro ou primeiro nome dentro da descricao
    return alvo in texto or alvo.split()[0] in texto.split()


_PADROES_COMANDO = (
    # LIGHT_OFF ANTES de LIGHT_ON: "desligue a luz" contem "ligue a luz"
    # como substring - se ON fosse testado primeiro, desligar LIGARIA a
    # lanterna. \w* cobre as conjugacoes (apaga/apague/apagar, desliga/
    # desligue...), que o padrao antigo [ae] nao cobria (bug real: "apague
    # a lanterna" caia na IA, achado no teste de 2026-07-19).
    (re.compile(r"apag\w*.*(lanterna|luz)|deslig\w*.*(lanterna|luz)"), "LIGHT_OFF"),
    (re.compile(r"acend\w*.*(lanterna|luz)|lig\w*.*(lanterna|luz)|luz.*lig"), "LIGHT_ON"),
    (re.compile(r"\bpar[ae]\b|\bparar\b|\bstop\b"), "STOP"),
    (re.compile(r"\bvarredura\b|\bvarrer\b|\bvarre\b|escanea\w*|\bscan\b"), "SCAN_FRONT"),
    (re.compile(r"anda[re]? para frente|va[i]? para frente|siga em frente"), "MOVE_FORWARD"),
    (re.compile(r"vir[ae] (a|para a) esquerda|gir[ae] (a|para a) esquerda"), "TURN_LEFT"),
    (re.compile(r"vir[ae] (a|para a) direita|gir[ae] (a|para a) direita"), "TURN_RIGHT"),
)

_RESPOSTAS_COMANDO = {
    "LIGHT_ON": "Lanterna ligada.",
    "LIGHT_OFF": "Lanterna desligada.",
    "STOP": "Parado.",
    "SCAN_FRONT": "Varrendo a frente.",
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
        diario=None,
    ) -> None:
        self._ai_manager = ai_manager
        self._enviar_comando_hardware = enviar_comando_hardware
        self._memory_client = memory_client
        # Diario das observacoes (camada 2). Sem ele o grounding recebe
        # lista vazia e o robo responde "nao tenho registro de hoje" - que
        # e correto, so que ele nunca teria registro de nada.
        self._diario = diario

    async def processar(self, texto_usuario: str, pessoa_id: int | None = None) -> str:
        contexto = await self._consultar_contexto(pessoa_id)

        comando_detectado = self._detectar_comando(texto_usuario)
        if comando_detectado is not None:
            resposta = await self._executar_comando(comando_detectado)
        elif self._eh_pergunta_de_hora(texto_usuario):
            resposta = self._responder_hora()
        elif (resposta_diario := await self._responder_sobre_quem_viu(
            texto_usuario, contexto
        )) is not None:
            resposta = resposta_diario
        else:
            resposta = await self._ai_manager.responder(texto_usuario, contexto)

        await self._registrar_interacao(pessoa_id, texto_usuario, resposta)
        return resposta

    async def _consultar_contexto(self, pessoa_id: int | None) -> dict | None:
        contexto: dict = {}

        if self._memory_client is not None:
            try:
                contexto.update(await self._memory_client.context(pessoa_id) or {})
            except Exception:
                logger.exception("Falha ao consultar contexto da memoria")

        # As observacoes entram SEMPRE que houver diario, mesmo vazias: a
        # lista vazia e o que o grounding transforma em "nao tenho NENHUM
        # registro hoje", e e justamente isso que impede a IA de inventar
        # uma visita. Omitir o campo faria o silencio voltar.
        if self._diario is not None:
            try:
                contexto["observacoes"] = await self._diario.observacoes_de_hoje()
            except Exception:
                logger.exception("Falha ao ler o diario de observacoes")
                contexto["observacoes"] = []

        return contexto or None

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

    async def _responder_sobre_quem_viu(
        self, texto: str, contexto: dict | None
    ) -> str | None:
        """Responde "voce viu o fulano hoje?" consultando o diario.

        POR QUE ISTO NAO VAI PARA A IA
        ------------------------------
        Isto e consulta a banco, nao tarefa de linguagem - e modelo pequeno
        erra justamente aqui. Medido em 2026-07-19 com o diario contendo
        APENAS "09:12 vi Bruno", perguntado sobre a Ana:

            gemma3:1b -> "Sim, vi. as 09:12."
            gemma3:4b -> "Sim, eu vi a Ana as 09:12!"

        Os dois viram um registro com hora e responderam que sim, sem
        conferir DE QUEM era. Nenhuma redacao de prompt conserta isso de
        forma confiavel: com enfase em nao inventar, o 1b passou a negar ate
        o que ESTAVA no diario; com enfase em usar os fatos, passou a
        afirmar o que nao estava. Ele segue o tom da instrucao, nao os
        dados.

        Entao a resposta e montada aqui, por comparacao de nome, e a IA
        fica com o que ela faz bem: conversa livre.

        Devolve None quando a pergunta nao e desse tipo - ai segue o fluxo
        normal.
        """
        if self._diario is None:
            return None

        nome = self._extrair_nome_perguntado(texto)
        if nome is None:
            return None

        observacoes = (contexto or {}).get("observacoes") or []
        encontrados = [
            o for o in observacoes
            if _mesmo_nome(nome, str(o.get("o_que", "")))
        ]

        if not encontrados:
            return f"Nao, hoje eu nao vi {nome} por aqui."
        if len(encontrados) == 1:
            return f"Vi sim! Foi as {encontrados[0].get('quando', '?')}."
        horas = ", ".join(o.get("quando", "?") for o in encontrados)
        return f"Vi sim, mais de uma vez: {horas}."

    @staticmethod
    def _extrair_nome_perguntado(texto: str) -> str | None:
        """Pega o nome em perguntas do tipo "voce viu a Ana hoje?"."""
        casado = _PADRAO_VIU_ALGUEM.search(texto)
        if casado is None:
            return None
        nome = casado.group("nome").strip(" ?.!,")
        # "alguem"/"alguma pessoa" nao e nome proprio - deixa para a IA.
        if not nome or nome.lower() in {"alguem", "alguém", "alguma coisa", "algo"}:
            return None
        return nome

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
