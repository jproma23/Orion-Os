"""Liga o Nav2 quando o robô precisa navegar e o desliga quando não precisa.

POR QUE EXISTE (2026-09-20, pedido do dono: *"o que consome muita ram só usar
no momento certo"*). O Nav2 vive num container Docker com
`restart: unless-stopped`: subia no boot do Pi e ficava de pé para sempre -
SLAM, planner, controller, costmap e bt_navigator girando com o robô parado
na bancada.

MEDIDO no Pi, parando e subindo o container de verdade:

    nav2 no ar      1686 MB livres
    nav2 parado     1911 MB livres
    ------------------------------
    custo            225 MB  (5,9% da RAM do Pi)

    subir ate o bridge_goal responder:  15 s

O numero de 225 MB e o que a MAQUINA sente. Somar o RSS dos 12 processos dava
386 MB e estava errado: RSS conta as bibliotecas do ROS, compartilhadas entre
eles, uma vez por processo.

POR QUE ISSO NAO CUSTA O MAPA: o slam_toolbox roda com `mode: mapping` e sem
`map_file_name` (ver nav2/config/nav2_params.yaml) - ele **nunca salva**. O
mapa ja era descartado a cada reinicio do container e a cada boot do Pi.
Desligar sob demanda nao perde nada que hoje sobreviva.

O PRECO E O ARRANQUE. O primeiro goal depois de um periodo ocioso espera os
15 s de subida. Quem chama decide o que dizer nesse meio tempo - este modulo
so avisa, por `navigation.nav2_subindo`, para o robô poder falar "já vou" em
vez de ficar mudo.
"""
from __future__ import annotations

import asyncio
import logging
import urllib.error
import urllib.request

from orion.kernel.event_bus import EventBus, Prioridade

logger = logging.getLogger("motion_core.navigation.gerente_nav2")

#: Nome do container (docker-compose projeto "nav2", servico "nav2").
NOME_CONTAINER = "nav2-nav2-1"

#: Onde o bridge_goal atende quando o Nav2 esta pronto de verdade. "Container
#: rodando" NAO e o mesmo que "Nav2 pronto": o docker devolve na hora e o ROS
#: ainda leva segundos para levantar os nos. Quem responde aqui e o sinal.
URL_SONDA = "http://127.0.0.1:8081/"

#: Teto da espera pela subida. Medido: 15 s. O dobro disso e folga para o Pi
#: sob carga (ele ja roda Whisper e YOLO ao mesmo tempo).
TIMEOUT_SUBIDA_S = 45.0
INTERVALO_SONDA_S = 1.0

#: `docker stop` manda SIGTERM e espera. Medido: 10,8 s para o Nav2 sair
#: limpo. Menos que isso vira SIGKILL no meio do desligamento dos nos ROS.
TIMEOUT_PARADA_S = 20.0


class GerenteNav2:
    """Dono do ciclo de vida do container do Nav2.

    Nao decide destino nem fala o protocolo do Nav2 - isso e do `Nav2Goal`.
    Aqui so se liga e se desliga.
    """

    def __init__(
        self,
        event_bus: EventBus,
        ocioso_para_desligar_s: float = 600.0,
        habilitado: bool = True,
    ) -> None:
        self._event_bus = event_bus
        self._ocioso_s = ocioso_para_desligar_s
        self._habilitado = habilitado
        #: De quanto em quanto tempo conferir a ociosidade. Um decimo da
        #: propria ociosidade (com o padrao de 600 s da 30 s, o teto): fino o
        #: bastante para nao segurar a RAM muito alem do combinado, e raro o
        #: bastante para nao custar nada. O piso baixo existe para o laco
        #: continuar testavel com ociosidades curtas.
        self._intervalo_checagem_s = min(30.0, max(0.01, ocioso_para_desligar_s / 10))
        self._ultimo_uso_s: float | None = None
        self._tarefa_ocioso: asyncio.Task | None = None
        #: Uma subida por vez. Dois goals chegando juntos depois de um periodo
        #: ocioso pediriam `docker start` em duplicata - o segundo falha com
        #: "container already started" e o goal seria descartado por engano.
        self._subindo: asyncio.Task | None = None

    async def _docker(self, *args: str, timeout_s: float) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            saida, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return 1, f"timeout de {timeout_s:.0f}s no docker {' '.join(args)}"
        return proc.returncode or 0, (saida or b"").decode(errors="replace").strip()

    async def esta_rodando(self) -> bool:
        codigo, saida = await self._docker(
            "inspect", "-f", "{{.State.Running}}", NOME_CONTAINER, timeout_s=10.0
        )
        return codigo == 0 and saida.strip() == "true"

    async def garantir_no_ar(self) -> bool:
        """Liga o Nav2 se preciso e so volta quando ele responder.

        Devolve False se nao conseguiu - quem chama trata como "nao da para
        navegar agora" em vez de mandar um goal para o vazio.
        """
        if not self._habilitado:
            # Modo antigo: o container vive de pe por conta propria.
            return True

        self.marcar_uso()
        if await self._bridge_pronto():
            return True

        # Uma subida por vez: quem chegar no meio espera a MESMA tarefa.
        if self._subindo is None or self._subindo.done():
            self._subindo = asyncio.create_task(self._subir())
        return await self._subindo

    async def _bridge_pronto(self) -> bool:
        return await asyncio.to_thread(_sondar, URL_SONDA)

    async def _subir(self) -> bool:
        logger.info("Nav2 esta parado - subindo (leva ~15 s)")
        await self._event_bus.publish(
            "navigation.nav2_subindo", {"espera_estimada_s": 15}, prioridade=Prioridade.ALTA
        )
        codigo, saida = await self._docker("start", NOME_CONTAINER, timeout_s=30.0)
        if codigo != 0:
            logger.error("falha ao subir o Nav2: %s", saida)
            return False

        loop = asyncio.get_running_loop()
        limite = loop.time() + TIMEOUT_SUBIDA_S
        while loop.time() < limite:
            if await self._bridge_pronto():
                decorrido = TIMEOUT_SUBIDA_S - (limite - loop.time())
                logger.info("Nav2 pronto em %.0f s", decorrido)
                self.marcar_uso()
                await self._event_bus.publish(
                    "navigation.nav2_pronto", {"segundos": round(decorrido)}
                )
                return True
            await asyncio.sleep(INTERVALO_SONDA_S)

        logger.error(
            "Nav2 subiu o container mas o bridge nao respondeu em %.0f s", TIMEOUT_SUBIDA_S
        )
        return False

    def marcar_uso(self) -> None:
        """Zera o relogio da ociosidade. Chamado a cada goal - e tambem
        enquanto um goal esta EM CURSO, senao uma navegacao longa (o timeout
        do goal e 600 s) seria desligada no meio do caminho."""
        try:
            self._ultimo_uso_s = asyncio.get_running_loop().time()
        except RuntimeError:
            self._ultimo_uso_s = None

    async def iniciar(self) -> None:
        """Laco que desliga o Nav2 depois de `ocioso_para_desligar_s` sem uso."""
        if not self._habilitado:
            return
        self._tarefa_ocioso = asyncio.current_task()
        while True:
            await asyncio.sleep(self._intervalo_checagem_s)
            if self._ultimo_uso_s is None:
                continue
            ocioso = asyncio.get_running_loop().time() - self._ultimo_uso_s
            if ocioso < self._ocioso_s:
                continue
            if not await self.esta_rodando():
                self._ultimo_uso_s = None
                continue
            logger.info("Nav2 ocioso ha %.0f s - desligando para devolver ~225 MB", ocioso)
            codigo, saida = await self._docker(
                "stop", "-t", "15", NOME_CONTAINER, timeout_s=TIMEOUT_PARADA_S
            )
            if codigo != 0:
                logger.warning("falha ao desligar o Nav2: %s", saida)
                continue
            self._ultimo_uso_s = None
            await self._event_bus.publish("navigation.nav2_desligado", {"ocioso_s": round(ocioso)})

    def parar(self) -> None:
        if self._tarefa_ocioso is not None:
            self._tarefa_ocioso.cancel()
            self._tarefa_ocioso = None


def _sondar(url: str) -> bool:
    """True se ALGUMA resposta HTTP veio - inclusive 404.

    O bridge_goal so tem rota para /goal e /cancelar; a raiz devolve 404. Um
    404 prova que ele esta de pe, que e a pergunta. Tratar 404 como "fora"
    faria o gerente esperar os 45 s inteiros e desistir de um Nav2 pronto.
    """
    try:
        with urllib.request.urlopen(url, timeout=2.0):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False
