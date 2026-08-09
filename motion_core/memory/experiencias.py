"""Registrador de experiencias: o que aconteceu, o que funcionou, o que falhou.

## O buraco que isto fecha

A tabela `experiencias` (Camada 1, 2026-08-09) existe no schema com o formato
exato do aprendizado por experiencia do dono — `situacao`, `o_que_aconteceu`,
`o_que_funcionou`, `o_que_falhou`, `resultado`, `contexto` — e NENHUM modulo
escrevia nela. A Camada 2 ligou o PORTÃO ("Eu já sei?") que LÊ o acervo antes
da IA; sem produtor, o acervo de experiencias fica vazio para sempre e o
portao so responde conceitos.

Este modulo e o produtor. Ele assina os eventos reais de desfecho do robo no
bus do Pi (`navigation.*`) e grava a experiencia ESTRUTURADA na tabela,
via `MemoryAPI` (regra 5: nenhum modulo abre banco direto).

## O que entra, e o que NÃO entra

- Entra o DESFECHO observado: obstaculo barrou o avanco, o alvo do follow se
  perdeu, um trecho concluiu, um comando falhou, um risco foi assumido.
- Só o que o evento REAL diz. `o_que_funcionou` / `o_que_falhou` saem dos
  campos do evento e da configuracao — nunca inferidos, nunca inventados.
  Preferir perder uma experiencia a inventar uma, a mesma regra do projeto.

## Por que ha janela de dedup (e ela e por CATEGORIA)

`navigation.obstacle_avoided` dispara a cada obstaculo, e a navegacao
replaneja e tenta de novo a cada ciclo. Gravar tudo produziria dezenas de
"avancei e havia obstaculo" por minuto — banco crescendo a toa e o FTS falando
so da mesma coisa. A janela por familia (`AVANCAR`, `FOLLOW`, `COMANDO`) e o
mesmo cuidado que o diario ja toma. Ela NAO impede a segunda tentativa de
virar experiencia nova: impede o despejo.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("motion_core.memory.experiencias")

CATEGORIA = "experiencias"

#: Familias de experiencia. A janela de dedup usa a familia, nao o texto:
#: saber que a familia AVANCAR falhou de novo e diferente de a mesma familia
#: falhar o dia inteiro num trecho so.
FAMILIA_OBSTACULO = "AVANCAR"
FAMILIA_FOLLOW = "FOLLOW"
FAMILIA_COMANDO = "COMANDO"
FAMILIA_RISCO = "RISCO"
FAMILIA_TRECHO = "TRECHO"

#: Janela de silencio por familia, em segundos. Obstaculos repetidos a cada
#: poucos segundos na mesma familia nao viram registro novo; um novo episodio
#: minutos depois e real.
JANELA_SILENCIO_S = 90.0

#: O que significa cada desfecho, para a coluna `resultado`. Seco e curto:
#: vai para dentro do index e do prompt.
RESULTADO_FALHOU = "falhou"
RESULTADO_FUNCIONOU = "funcionou"
RESULTADO_RISCO = "risco_assumido"


class RegistradorExperiencias:
    """Assina desfechos reais e grava experiencias estruturadas no banco."""

    def __init__(
        self,
        event_bus: EventBus,
        memoria,
        janela_s: float = JANELA_SILENCIO_S,
        agora=None,
    ) -> None:
        self._bus = event_bus
        self._memoria = memoria
        self._janela_s = janela_s
        self._agora = agora or datetime.now
        self._ultima: dict[str, datetime] = {}

        # A FAMILIA da experiencia vem do topico; cada topico tem seu evento.
        event_bus.subscribe("navigation.obstacle_avoided", self._ao_obstaculo)
        event_bus.subscribe("navigation.target_lost", self._ao_perder_alvo)
        event_bus.subscribe("navigation.error", self._ao_erro)
        event_bus.subscribe("navigation.risco_assumido", self._ao_risco)
        event_bus.subscribe("navigation.segment_completed", self._ao_trecho_concluido)

    # ----- captura -----

    async def _ao_obstaculo(self, evento: Evento) -> None:
        if not self._passou_janela(FAMILIA_OBSTACULO):
            return
        distancia = evento.dados.get("distancia_frontal_cm")
        motivo = str(evento.dados.get("motivo") or "obstaculo_frontal")
        falha = f"obstaculo a {float(distancia):.0f} cm" if distancia is not None else "frente obstruida"
        await self._gravar(
            situacao="avancar",
            acontecimento=f"nao pode avancar - {falha}",
            funcionou="",
            falhou=falha,
            resultado=RESULTADO_FALHOU,
            contexto=f"motivo:{motivo}",
        )

    async def _ao_perder_alvo(self, evento: Evento) -> None:
        if not self._passou_janela(FAMILIA_FOLLOW):
            return
        await self._gravar(
            situacao="seguir_pessoa",
            acontecimento="perdeu o alvo durante o acompanhamento",
            funcionou="",
            falhou="perdeu o alvo; velocidade ou iluminacao a verificar",
            resultado=RESULTADO_FALHOU,
            contexto="follow_lost",
        )

    async def _ao_erro(self, evento: Evento) -> None:
        if not self._passou_janela(FAMILIA_COMANDO):
            return
        acao = str(evento.dados.get("acao") or "acao_desconhecida")
        motivo = str(evento.dados.get("motivo") or "erro sem motivo")
        await self._gravar(
            situacao=acao,
            acontecimento=f"o comando falhou: {motivo}",
            funcionou="",
            falhou=motivo,
            resultado=RESULTADO_FALHOU,
            contexto=f"acao:{acao}",
        )

    async def _ao_risco(self, evento: Evento) -> None:
        if not self._passou_janela(FAMILIA_RISCO):
            return
        contexto = str(evento.dados.get("contexto") or "desconhecido")
        motivo = str(evento.dados.get("motivo") or "frente_desconhecida")
        angulos = int(evento.dados.get("angulos_varridos") or 0)
        await self._gravar(
            situacao=f"avancar_sem_medida ({contexto})",
            acontecimento="avancou sem medida frontal confiavel",
            funcionou="",
            falhou="avancou sem saber o que havia na frente",
            resultado=RESULTADO_RISCO,
            contexto=f"motivo:{motivo};angulos_varridos:{angulos}",
        )

    async def _ao_trecho_concluido(self, evento: Evento) -> None:
        if not self._passou_janela(FAMILIA_TRECHO):
            return
        graus = evento.dados.get("graus")
        distancia = evento.dados.get("distancia_cm")
        descricao = self._descrever_trecho(graus, distancia)
        await self._gravar(
            situacao="mover",
            acontecimento=f"{descricao} concluido",
            funcionou="trecho concluido sem obstaculo",
            falhou="",
            resultado=RESULTADO_FUNCIONOU,
            contexto=f"graus:{graus};distancia_cm:{distancia}",
        )

    # ----- apoio -----

    def _descrever_trecho(self, graus: Any, distancia: Any) -> str:
        partes: list[str] = []
        if distancia is not None:
            partes.append(f"{float(distancia):.0f} cm")
        if graus is not None:
            partes.append(f"girando {float(graus):.0f}°")
        return " e ".join(partes) if partes else "trecho"

    def _passou_janela(self, familia: str) -> bool:
        agora = self._agora()
        anterior = self._ultima.get(familia)
        if anterior is None:
            self._ultima[familia] = agora
            return True
        if (agora - anterior).total_seconds() < self._janela_s:
            return False
        self._ultima[familia] = agora
        return True

    async def _gravar(self, **campos: str) -> None:
        """Grava uma experiencia. Falha de memoria NAO pode derrubar o robo."""
        try:
            await self._memoria.saber_guardar(CATEGORIA, campos)
            logger.info(
                "experiencia gravada: %s - %s (%s)",
                campos.get("situacao", "?"),
                campos.get("resultado", "?"),
                campos.get("falhou", campos.get("funcionou", "")),
            )
        except Exception:
            logger.warning("nao consegui gravar a experiencia", exc_info=True)