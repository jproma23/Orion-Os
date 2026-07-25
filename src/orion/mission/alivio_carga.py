"""Alívio de carga do Notebook (EDR-0020, Cap 16).

QUEM PEDE E QUEM ATENDE
-----------------------
O Guardião de RAM roda no Raspberry (nó estável), vigia a memória livre que
o Notebook reporta e publica `behavior.reduzir_carga_ia` quando a folga cai
abaixo do limiar crítico. Este módulo é a outra ponta: quem ATENDE o pedido.

Até 2026-07-19 essa outra ponta não existia - o guardião publicava o pedido
e o evento morria no barramento (só os testes escutavam). Ou seja: o
mecanismo inteiro de proteção contra travamento por falta de memória estava
desligado sem ninguém perceber, porque publicar um evento não falha.

O QUE SE SACRIFICA, E POR QUÊ NESSA ORDEM
-----------------------------------------
1. **Modelo de IA sai da RAM** (~880 MB do gemma3:1b, que fica residente por
   causa do keep_alive de 30min). É o maior ganho pelo menor prejuízo: a
   próxima pergunta recarrega sozinha, custando só o tempo de carga.
2. **Sentinela de visão pausa** (reconhecimento facial é a parte mais cara
   em CPU e memória). Ficar cego alguns minutos é melhor que travar a
   máquina - travada, ele fica cego do mesmo jeito, e surdo também.

O que NÃO se desliga: a voz. Se o dono chamar "Fofão" no meio de um aperto
de memória, o robô tem que responder - é a função mais básica dele.

Ao voltar a folga, o guardião publica `diagnostic.recuperado` e tudo é
restaurado.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("orion.mission.alivio_carga")

TOPICO_PEDIDO_ALIVIO = "behavior.reduzir_carga_ia"
TOPICO_RECUPERADO = "diagnostic.recuperado"


class AlivioCarga:
    """Atende os pedidos de alívio vindos do Guardião de RAM do Pi."""

    def __init__(
        self,
        event_bus: EventBus,
        descarregar_modelo: Callable[[], "Awaitable[None]"] | None = None,
        pausar_visao: Callable[[], None] | None = None,
        retomar_visao: Callable[[], None] | None = None,
    ) -> None:
        self._bus = event_bus
        self._descarregar_modelo = descarregar_modelo
        self._pausar_visao = pausar_visao
        self._retomar_visao = retomar_visao
        self._aliviado = False

        event_bus.subscribe(TOPICO_PEDIDO_ALIVIO, self._ao_pedir_alivio)
        event_bus.subscribe(TOPICO_RECUPERADO, self._ao_recuperar)

    @property
    def aliviado(self) -> bool:
        return self._aliviado

    async def _ao_pedir_alivio(self, evento: Evento) -> None:
        # Idempotente: o guardião só publica na transição, mas o evento pode
        # chegar repetido pelo link. Aliviar duas vezes não ajuda em nada.
        if self._aliviado:
            return
        self._aliviado = True

        ram = evento.dados.get("ram_livre_mb")
        logger.warning(
            "ALÍVIO DE CARGA acionado (RAM livre do Notebook: %s MB) - "
            "descarregando modelo e pausando a visão",
            ram,
        )

        # Cada ação é isolada: se descarregar o modelo falhar, a visão ainda
        # deve pausar. Numa situação de memória crítica, alívio parcial é
        # melhor que nenhum.
        if self._descarregar_modelo is not None:
            try:
                await self._descarregar_modelo()
                logger.info("modelo de IA descarregado da RAM")
            except Exception:
                logger.exception("falha ao descarregar o modelo de IA")

        if self._pausar_visao is not None:
            try:
                self._pausar_visao()
            except Exception:
                logger.exception("falha ao pausar a sentinela de visão")

    async def _ao_recuperar(self, evento: Evento) -> None:
        # `diagnostic.recuperado` é usado por outras origens também - só
        # reage ao que veio do guardião de RAM.
        if evento.dados.get("origem") != "guardiao_ram":
            return
        if not self._aliviado:
            return
        self._aliviado = False

        logger.info(
            "RAM do Notebook recuperada (%s MB) - retomando carga normal",
            evento.dados.get("ram_livre_mb"),
        )
        # O modelo não precisa ser recarregado à mão: a próxima pergunta faz
        # isso sozinha. Só a visão precisa ser religada explicitamente.
        if self._retomar_visao is not None:
            try:
                self._retomar_visao()
            except Exception:
                logger.exception("falha ao retomar a sentinela de visão")
