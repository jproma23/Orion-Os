"""Diário do robô: o que o Fofão viu, gravado e recuperável (camada 2).

O QUE FALTAVA
-------------
A camada 2 do grounding (`orion/mission/grounding.py`) já sabia formatar
observações e, mais importante, sabia dizer "não tenho NENHUM registro
hoje". Só que nada nunca preenchia essa lista - ela era sempre vazia. O
robô era honesto por falta de memória, não por ter olhado.

Este módulo é quem escreve o diário e quem o lê de volta.

O QUE ENTRA NO DIÁRIO (e o que não entra)
------------------------------------------
Entra o que uma pessoa perguntaria depois: quem apareceu, quando, e se foi
alguém reconhecido. Não entra telemetria, estado de motor nem nada de alta
frequência - isso já vive na tabela `telemetria` e encheria o contexto da
IA de ruído, empurrando para fora justamente o que importa.

O CUIDADO QUE MAIS IMPORTA: NÃO INUNDAR
----------------------------------------
`vision.person_detected` dispara a cada verificação enquanto a pessoa
estiver na frente da câmera. Gravar tudo produziria centenas de linhas
"vi o João Paulo" por hora - o banco cresceria à toa e o bloco de contexto
viraria uma parede de repetição, que é a melhor forma de fazer um modelo
pequeno ignorar o que interessa. Por isso há uma janela de silêncio por
pessoa: revê-la dentro dela não vira registro novo.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("orion.mission.diario")

CATEGORIA = "eventos"
ORIGEM = "diario"

#: Tipos gravados no diário. Nomes curtos porque vão para dentro do prompt.
TIPO_PESSOA_VISTA = "pessoa_vista"
TIPO_DESCONHECIDO = "desconhecido_visto"

#: Janela de silêncio por pessoa. Ver o João Paulo de novo 2 minutos depois
#: não é fato novo - é a mesma presença continuando.
JANELA_SILENCIO_S = 600.0

#: Teto de registros lidos para montar o contexto. O bloco vai inteiro para
#: dentro do prompt de um modelo de 1B com janela pequena: melhor poucos
#: registros recentes e legíveis do que um despejo que empurra o resto para
#: fora da janela.
MAX_OBSERVACOES_CONTEXTO = 8


class DiarioObservacoes:
    """Escreve no diário a partir dos eventos, e lê de volta para a IA."""

    def __init__(
        self,
        event_bus: EventBus,
        memory_client,
        janela_silencio_s: float = JANELA_SILENCIO_S,
        agora=None,
    ) -> None:
        self._bus = event_bus
        self._memoria = memory_client
        self._janela_silencio_s = janela_silencio_s
        # injetável para os testes não dependerem do relógio real
        self._agora = agora or datetime.now
        #: última vez que cada pessoa virou registro (nome -> datetime)
        self._ultimo_registro: dict[str, datetime] = {}

        event_bus.subscribe("vision.person_detected", self._ao_ver_pessoa)
        event_bus.subscribe("sentinela.alerta", self._ao_receber_alerta)

    # ----- escrita -----

    async def _ao_ver_pessoa(self, evento: Evento) -> None:
        nome = evento.dados.get("nome")
        # Rosto sem nome é tratado pelo alerta da sentinela, não aqui - se
        # gravasse nos dois lugares, o mesmo estranho apareceria duas vezes
        # no contexto e a IA poderia contar como duas visitas.
        if not nome:
            return
        if not self._passou_a_janela(nome):
            return
        await self._gravar(TIPO_PESSOA_VISTA, {"nome": nome})

    async def _ao_receber_alerta(self, evento: Evento) -> None:
        if evento.dados.get("tipo") != "pessoa":
            return  # alerta de barulho etc. não é "alguém apareceu"
        if not self._passou_a_janela("__desconhecido__"):
            return
        await self._gravar(TIPO_DESCONHECIDO, {})

    def _passou_a_janela(self, chave: str) -> bool:
        agora = self._agora()
        anterior = self._ultimo_registro.get(chave)
        if anterior is not None:
            if (agora - anterior).total_seconds() < self._janela_silencio_s:
                return False
        self._ultimo_registro[chave] = agora
        return True

    async def _gravar(self, tipo: str, dados: dict[str, Any]) -> None:
        try:
            await self._memoria.remember(
                CATEGORIA,
                {
                    "origem": ORIGEM,
                    "tipo": tipo,
                    "payload_json": json.dumps(dados, ensure_ascii=False),
                },
            )
            logger.info("diário: %s %s", tipo, dados or "")
        except Exception:
            # Memória fora do ar não pode derrubar a visão nem a conversa -
            # o robô fica sem diário, o que o grounding já sabe reportar
            # ("não tenho registro"), em vez de mentir.
            logger.warning("falha ao gravar no diário (%s)", tipo, exc_info=True)

    # ----- leitura -----

    async def observacoes_de_hoje(self) -> list[dict[str, str]]:
        """Registros de hoje, no formato que o grounding espera.

        Devolve lista vazia quando não há nada - e isso É informação: o
        grounding a transforma em "não tenho NENHUM registro hoje", que é
        o que impede a IA de inventar uma visita.
        """
        try:
            registros = await self._memoria.recall(
                CATEGORIA, filtro={"origem": ORIGEM}, limite=MAX_OBSERVACOES_CONTEXTO * 3
            )
        except Exception:
            logger.warning("falha ao ler o diário", exc_info=True)
            return []

        inicio_do_dia = self._agora().replace(hour=0, minute=0, second=0, microsecond=0)
        observacoes: list[dict[str, str]] = []

        for registro in registros:
            quando = _parse_timestamp(registro.get("timestamp"))
            if quando is None or quando < inicio_do_dia:
                continue  # ontem não conta como "hoje"
            observacoes.append(
                {"quando": quando.strftime("%H:%M"), "o_que": _descrever(registro)}
            )
            if len(observacoes) >= MAX_OBSERVACOES_CONTEXTO:
                break

        # recall devolve o mais recente primeiro; no texto a ordem
        # cronológica é mais natural de ler (e de a IA narrar).
        observacoes.reverse()
        return observacoes


def _parse_timestamp(valor: Any) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return None


def _descrever(registro: dict[str, Any]) -> str:
    """Vira frase curta em português - o texto entra direto no prompt."""
    tipo = registro.get("tipo")
    try:
        dados = json.loads(registro.get("payload_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        dados = {}

    if tipo == TIPO_PESSOA_VISTA:
        return f"vi {dados.get('nome', 'alguém')}"
    if tipo == TIPO_DESCONHECIDO:
        return "vi uma pessoa que não reconheci"
    return str(tipo)


def hoje_ate(agora: datetime, horas: int) -> datetime:
    """Auxiliar de teste/consulta: instante de N horas atrás."""
    return agora - timedelta(hours=horas)
