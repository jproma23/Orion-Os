"""Mapear a casa inteira: o robô sai sozinho até onde o mapa acaba.

Pedido do dono (2026-11-14): *"pedi para ele mapear a casa inteira e disse q
os sensores ainda nao consegui"* / *"vamos fazer isso acontecer"*. O robo nao
mentiu: nao havia acao nenhuma para isso. Os sensores estavam bons (LiDAR,
bussola e encoder medindo); faltava o caminho.

As pecas ja existiam e nao se falavam:

- `nav2/src/orion_nav2/orion_nav2/explorar.py` - o explorador de FRONTEIRAS
  (rodou em 31/10 e salvou `data/mapas/casa.pgm`), mas so a mao, por
  `docker exec`, e fora do master (branch wip-dono-plugins-2026-11-08);
- `GerenteNav2` - sobe e desliga o container sob demanda;
- o ponto unico de parada (`navigation.parar`).

Este modulo junta as tres. Ao receber `navigation.mapear`:

1. sobe o Nav2 (se estiver desligado: ~15 s);
2. guarda o mapa anterior com a data (o SLAM comeca do zero a cada subida do
   container - sem isto o mapa velho seria sobrescrito sem aviso);
3. apaga o arquivo PARAR que tenha sobrado (o de 01/11 ainda estava la: o
   explorador sairia no primeiro segundo sem mexer uma roda);
4. roda o explorador DENTRO do container e espera ele terminar, segurando o
   Nav2 ligado enquanto isso (senao o gerente o desligaria no meio por
   ociosidade);
5. no fim, le o log e publica `navigation.mapeamento_terminado` - quem
   assina e a webui, que o guarda em `/estado` -> navegacao.mapeamento.

Quem publica `navigation.mapear` e o NavigationCore, ao receber a acao
MAPEAR (a fala "mapeia a casa" chega como `navigation.comando`). O mesmo
evento solta as duas travas de VELOCIDADE que um STOP anterior deixou (a do
NavigationCore e a da webui): e um pedido explicito de "Nav2, dirija", como
o goal novo. Sem isso, o explorador mandaria alvos e as rodas nao girariam.

PARAR: `navigation.parar` (STOP falado, botao da webui, HOLD) cria o arquivo
PARAR. O explorador o confere a cada 0,2 s, cancela o alvo do Nav2, salva o
mapa e sai. O STOP do NavigationCore ja trava a VELOCIDADE ao mesmo tempo -
mesmo que o explorador demorasse, as rodas ja estao paradas.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from motion_core.navigation.gerente_nav2 import NOME_CONTAINER
from orion.kernel.event_bus import EventBus, Evento, Prioridade
from orion.kernel.tarefas import disparar

logger = logging.getLogger("motion_core.navigation.mapeamento")

#: `data/mapas` no Pi = `/opt/orion_nav2/mapas` no container (docker-compose).
PASTA_MAPAS_HOST = Path(__file__).resolve().parents[2] / "data" / "mapas"
PASTA_MAPAS_CONTAINER = "/opt/orion_nav2/mapas"
SCRIPT_EXPLORADOR = "/opt/orion_nav2/src/orion_nav2/orion_nav2/explorar.py"
NOME_MAPA = "casa"
ARQUIVO_PARAR = "PARAR"
ARQUIVO_LOG = "explorar.log"

#: Teto da exploracao inteira. O explorador tem o dele (40 alvos x 120 s =
#: 80 min no pior caso); este e so para nunca ficar preso esperando um
#: processo que travou.
TIMEOUT_EXPLORACAO_S = 90 * 60.0
#: Depois do PARAR, quanto esperar o explorador sair sozinho (ele cancela o
#: alvo e salva o mapa: medido ~2 s em 01/11) antes de matar o processo.
TIMEOUT_SAIR_S = 40.0
#: Bem menor que a ociosidade do gerente (10 min no orion.yaml).
INTERVALO_MANTER_VIVO_S = 30.0


class Mapeamento:
    """Dono da exploracao da casa. Um mapeamento por vez."""

    def __init__(
        self,
        event_bus: EventBus,
        gerente: Any | None = None,
        pasta_mapas: Path = PASTA_MAPAS_HOST,
    ) -> None:
        self._event_bus = event_bus
        #: GerenteNav2, ou None quando o Nav2 vive de pe sozinho
        #: (`nav2.sob_demanda: false`).
        self._gerente = gerente
        self._pasta = Path(pasta_mapas)
        self._tarefa: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._parar_pedido = False
        #: O ultimo resultado, para a webui e para quem perguntar "e o mapa?".
        self.ultimo: dict[str, Any] | None = None
        event_bus.subscribe("navigation.mapear", self._ao_mapear)
        event_bus.subscribe("navigation.parar", self._ao_parar)

    @property
    def em_andamento(self) -> bool:
        return self._tarefa is not None and not self._tarefa.done()

    async def _ao_mapear(self, evento: Evento) -> None:
        if self.em_andamento:
            logger.info("mapeamento: ja estou mapeando - pedido repetido ignorado")
            return
        self._parar_pedido = False
        self._tarefa = disparar(self._mapear(), "mapeamento")

    async def _ao_parar(self, evento: Evento) -> None:
        if not self.em_andamento:
            return
        self._parar_pedido = True
        logger.info("mapeamento: PARAR (origem=%s)", evento.dados.get("origem"))
        self._criar_parar()

    def _criar_parar(self) -> None:
        try:
            (self._pasta / ARQUIVO_PARAR).touch()
        except OSError as erro:
            logger.error("mapeamento: nao consegui criar o PARAR: %s", erro)

    def _preparar_pasta(self) -> None:
        """Guarda o mapa anterior com a data e tira o PARAR que sobrou."""
        self._pasta.mkdir(parents=True, exist_ok=True)
        (self._pasta / ARQUIVO_PARAR).unlink(missing_ok=True)
        carimbo = time.strftime("%Y%m%d_%H%M%S")
        for ext in (".pgm", ".yaml"):
            antigo = self._pasta / f"{NOME_MAPA}{ext}"
            if antigo.exists():
                # rename e nao copia: o .pgm e do root (salvo de dentro do
                # container), mas renomear so pede escrita na PASTA.
                antigo.rename(self._pasta / f"{NOME_MAPA}_{carimbo}{ext}")

    async def _mapear(self) -> None:
        inicio = time.monotonic()
        if self._gerente is not None and not await self._gerente.garantir_no_ar():
            await self._terminar("falhou", inicio, "o Nav2 nao subiu")
            return
        if self._parar_pedido:
            # "para" durante os 15 s de subida do Nav2.
            await self._terminar("parado", inicio, "parado antes de comecar")
            return
        try:
            self._preparar_pasta()
        except OSError as erro:
            await self._terminar("falhou", inicio, f"pasta de mapas: {erro}")
            return

        vivo = disparar(self._manter_vivo(), "mapeamento_vivo") if self._gerente else None
        try:
            codigo = await self._rodar_explorador()
        finally:
            if vivo is not None:
                vivo.cancel()

        log = self._ler_log()
        if self._parar_pedido:
            resultado = "parado"
        elif codigo == 0 and "exploracao COMPLETA" in log:
            resultado = "completo"
        elif codigo == 0:
            # Saiu limpo sem achar o fim: o teto de alvos do explorador.
            resultado = "incompleto"
        else:
            resultado = "falhou"
        await self._terminar(resultado, inicio, _ultima_linha(log) or f"codigo {codigo}")

    async def _rodar_explorador(self) -> int:
        comando = (
            "source /opt/ros/humble/setup.bash && "
            f"python3 {SCRIPT_EXPLORADOR} > {PASTA_MAPAS_CONTAINER}/{ARQUIVO_LOG} 2>&1"
        )
        logger.info("mapeamento: explorador no ar")
        self._proc = await asyncio.create_subprocess_exec(
            "docker", "exec", NOME_CONTAINER, "bash", "-c", comando,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return await asyncio.wait_for(self._proc.wait(), timeout=TIMEOUT_EXPLORACAO_S)
        except TimeoutError:
            logger.error(
                "mapeamento: explorador passou de %.0f min - parando",
                TIMEOUT_EXPLORACAO_S / 60,
            )
            self._criar_parar()
            try:
                return await asyncio.wait_for(self._proc.wait(), timeout=TIMEOUT_SAIR_S)
            except TimeoutError:
                self._proc.kill()
                return await self._proc.wait()
        finally:
            self._proc = None

    async def _manter_vivo(self) -> None:
        while True:
            self._gerente.marcar_uso()
            await asyncio.sleep(INTERVALO_MANTER_VIVO_S)

    def _ler_log(self) -> str:
        try:
            return (self._pasta / ARQUIVO_LOG).read_text(errors="replace")
        except OSError:
            return ""

    async def _terminar(self, resultado: str, inicio: float, detalhe: str) -> None:
        # Conta so a linha que ABRE o alvo ("alvo 3: (x, y), ..."); a do
        # resultado ("alvo 3: chegou") nao tem o parentese.
        alvos = len(re.findall(r": alvo \d+: \(", self._ler_log()))
        self.ultimo = {
            "resultado": resultado,
            "detalhe": detalhe,
            "alvos": alvos,
            "minutos": round((time.monotonic() - inicio) / 60.0, 1),
            "mapa": str(self._pasta / f"{NOME_MAPA}.pgm"),
            "quando": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        logger.info("mapeamento %s: %s", resultado, self.ultimo)
        await self._event_bus.publish(
            "navigation.mapeamento_terminado", dict(self.ultimo), prioridade=Prioridade.ALTA
        )


def _ultima_linha(log: str) -> str:
    linhas = [linha.strip() for linha in log.splitlines() if linha.strip()]
    return linhas[-1] if linhas else ""
