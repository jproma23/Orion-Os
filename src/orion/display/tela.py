"""Descanso de tela do ORION (Cap 13 s.3).

Pedido do dono em 2026-08-07: *"poem tambem um descanso de tela pois
precisamos economizar energia"*, e a regra que inverte o desenho antigo:
*"o robo fica maior parte do tempo com tela apagada, so vai abrir a tela
quando solicitado"*, *"3 minutos a tela descansa"*.

## O que estava errado antes deste arquivo

Duas coisas se contradiziam, e quem lesse so uma concluiria errado:

1. `config/orion.yaml` prometia `sleep_timeout_min: 15` - e NINGUEM lia esse
   numero. Era mais uma peca pronta que ninguem chamava.
2. `~/.config/autostart/orion-avatar-kiosk.desktop` fazia o oposto, de
   proposito: `xset s off; xset s noblank; xset -dpms`.

A config prometia dormir, o autostart garantia que nunca dormia.

## Por que nao basta reativar o DPMS do X

O X so conta teclado e mouse, e ninguem toca em nenhum dos dois: a tela
apagaria no meio de uma conversa, com a pessoa falando na frente do robo.
Trocar "nunca dorme" por "dorme na hora errada" nao seria conserto.

Quem sabe se algo esta acontecendo e o Event Bus. Por isso o temporizador
mora aqui, alimentado por eventos, e o protetor de tela do X continua
desligado (`xset s off`): quem decide e o robo.

## As tres regras, e a diferenca entre elas e o coracao deste modulo

- `acordar()`  - ACENDE. So para o que o dono pediu para ver: o quadro
  (uma aula, um grafico) e o pedido explicito de acender a tela.
- `adiar()`    - NAO acende; so empurra o prazo de quem JA esta aceso. E o
  que impede a tela de apagar no meio de uma aula em que o robo passou dois
  minutos falando sem escrever nada.
- `apagar()`   - apaga na hora (pedido, ou prazo vencido).

Acender em evento comum (pessoa vista, voz ouvida, telemetria) seria ter a
tela ligada o dia inteiro por outro caminho - era o erro do desenho
anterior, e e por isso que `adiar()` existe separado de `acordar()`.

## Falhar aqui nao pode emudecer o robo

Sem `xset` (o Pi nao tem X, e a suite tambem nao), o controle se declara
inativo UMA VEZ no log e vira um objeto que nao faz nada. A tela e a cara do
robo: o pior desfecho possivel deste arquivo seria um Fofao de rosto preto
na sala por causa de um binario ausente.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger("orion.display.tela")

#: Eventos que ACENDEM a tela. Lista fechada e curta de proposito - ver o
#: cabecalho: acender no que acontece o dia todo e o mesmo que nunca apagar.
#:
#: `system.ready` esta aqui por um motivo diferente dos outros: ele acontece
#: UMA vez, na subida, e serve de prova de vida - quem liga o robo ve o rosto
#: dele antes de a tela descansar.
TOPICOS_QUE_ACORDAM = (
    "display.quadro",
    "display.tela",
    "system.ready",
)

#: Eventos que apenas EMPURRAM o prazo de uma tela que ja esta acesa. Nao
#: acendem nada. A fala entra aqui porque uma explicacao longa e silencio no
#: quadro: sem isto, a tela apagaria no meio da aula.
TOPICOS_QUE_ADIAM = (
    "voice.status",
    "voice.response_started",
    "voice.saudacao",
)

#: De quanto em quanto tempo o laco confere se o prazo venceu. Um segundo e
#: folgado para um prazo medido em minutos e nao custa nada; a alternativa
#: (cancelar e recriar uma tarefa a cada evento) cria e destroi tarefa em
#: cada frase falada.
_INTERVALO_CONFERENCIA_S = 1.0


class ControleTela:
    """Liga e desliga o monitor por `xset`, com prazo alimentado por eventos.

    O relogio e o executor entram pelo construtor para o teste poder
    controlar os dois: sem isso, provar "apaga depois de 3 minutos" custaria
    3 minutos de suite.
    """

    def __init__(
        self,
        minutos_ate_dormir: float = 3.0,
        *,
        habilitado: bool = True,
        display: str = ":0.0",
        xauthority: str | None = None,
        executar: Callable[[tuple[str, ...]], Awaitable[bool]] | None = None,
        agora: Callable[[], float] = time.monotonic,
    ) -> None:
        self._prazo_s = max(0.0, float(minutos_ate_dormir) * 60.0)
        self._display = display
        self._xauthority = xauthority or os.path.expanduser("~/.Xauthority")
        self._executar = executar or self._executar_xset
        self._agora = agora
        #: Comeca APAGADA no registro interno, e nao "desconhecida": assim a
        #: primeira chamada a `acordar()` realmente manda o `xset`, em vez de
        #: achar que a tela ja estava acesa e nao fazer nada. Estado errado
        #: para o lado seguro.
        self._acesa = False
        self._vence_em = 0.0
        self._tarefa: asyncio.Task | None = None
        self._habilitado = habilitado
        #: Contadores para responder "a tela apagou sozinha ou alguem a
        #: apagou?" pelo log, sem precisar estar na frente do robo.
        self.acendeu_vezes = 0
        self.apagou_vezes = 0

        if habilitado and shutil.which("xset") is None:
            # Caminho normal no Pi e na suite: nao ha X aqui. Nao e erro.
            logger.info("sem 'xset' nesta maquina - descanso de tela inativo")
            self._habilitado = False

    @property
    def acesa(self) -> bool:
        return self._acesa

    @property
    def ativo(self) -> bool:
        return self._habilitado

    async def _executar_xset(self, argumentos: tuple[str, ...]) -> bool:
        """Chama o `xset` no X do kiosk. Devolve se deu certo.

        `DISPLAY` e `XAUTHORITY` vao explicitos porque o `orion-avatar.service`
        e um servico de usuario e nao herda nenhum dos dois - sem eles o xset
        responde "unable to open display" e a tela nunca obedece.
        """
        ambiente = {**os.environ, "DISPLAY": self._display, "XAUTHORITY": self._xauthority}
        try:
            processo = await asyncio.create_subprocess_exec(
                "xset", *argumentos,
                env=ambiente,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, erro = await processo.communicate()
        except Exception:
            logger.exception("falha ao chamar xset %s", " ".join(argumentos))
            return False
        if processo.returncode != 0:
            logger.warning(
                "xset %s falhou (codigo %s): %s",
                " ".join(argumentos), processo.returncode, erro.decode(errors="replace").strip(),
            )
            return False
        return True

    async def acordar(self, motivo: str = "") -> None:
        """Acende a tela e reinicia o prazo."""
        self._vence_em = self._agora() + self._prazo_s
        if self._acesa or not self._habilitado:
            return
        if await self._executar(("dpms", "force", "on")):
            self._acesa = True
            self.acendeu_vezes += 1
            logger.info("tela ACESA (%s) - descansa em %.0f s", motivo, self._prazo_s)

    async def adiar(self, motivo: str = "") -> None:
        """Empurra o prazo, SEM acender. Tela apagada continua apagada.

        Esta e a linha que separa "a tela acende quando ha o que mostrar" de
        "a tela fica acesa o dia todo": aqui entram os eventos que acontecem
        o tempo inteiro.
        """
        if not self._acesa:
            return
        self._vence_em = self._agora() + self._prazo_s

    async def apagar(self, motivo: str = "") -> None:
        if not self._acesa or not self._habilitado:
            return
        if await self._executar(("dpms", "force", "off")):
            self._acesa = False
            self.apagou_vezes += 1
            logger.info("tela APAGADA (%s)", motivo)

    async def conferir_prazo(self) -> None:
        """Apaga se o prazo venceu. Chamada pelo laco - e pelo teste."""
        if self._acesa and self._agora() >= self._vence_em:
            await self.apagar("prazo vencido")

    def ligar_no_bus(self, event_bus) -> None:
        """Assina os dois grupos de topicos.

        As duas listas ficam separadas e fechadas (mesma ideia do catalogo de
        acoes faladas): topico que nao esta em nenhuma das duas nao mexe na
        tela, e isso e proposital, nao esquecimento.
        """
        for topico in TOPICOS_QUE_ACORDAM:
            event_bus.subscribe(topico, self._fazer_tratador(topico, acende=True))
        for topico in TOPICOS_QUE_ADIAM:
            event_bus.subscribe(topico, self._fazer_tratador(topico, acende=False))

    def _fazer_tratador(self, topico: str, *, acende: bool):
        async def _tratar(evento) -> None:
            dados = getattr(evento, "dados", None) or {}
            # `display.tela` e o unico topico com as duas direcoes: e o pedido
            # explicito, e quem pede para APAGAR tem que ser obedecido na hora
            # (senao "apaga a tela" so apagaria 3 minutos depois).
            if topico == "display.tela" and dados.get("ligada") is False:
                await self.apagar("pedido")
                return
            if acende:
                await self.acordar(topico)
            else:
                await self.adiar(topico)

        return _tratar

    async def iniciar(self) -> None:
        if not self._habilitado:
            return
        # Deixa o X em estado conhecido: protetor de tela do X DESLIGADO (quem
        # decide o momento e este modulo, nao o temporizador do X) e DPMS
        # LIGADO (sem ele, `dpms force off` nao tem efeito nenhum - era
        # exatamente o que o `xset -dpms` do autostart causava).
        await self._executar(("s", "off"))
        await self._executar(("s", "noblank"))
        await self._executar(("+dpms",))
        # Zera os tempos de standby/suspend/off do X. Sem isto, o X ainda
        # apagaria a tela sozinho pelos SEUS proprios contadores, por baixo
        # da decisao do robo - dois donos para a mesma tela.
        await self._executar(("dpms", "0", "0", "0"))
        await self.acordar("subida")
        self._tarefa = asyncio.create_task(self._laco())

    async def _laco(self) -> None:
        try:
            while True:
                await asyncio.sleep(_INTERVALO_CONFERENCIA_S)
                await self.conferir_prazo()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Um erro aqui matava o laco em silencio e a tela ficaria acesa
            # para sempre - a falha silenciosa de sempre, so que cara em
            # energia.
            logger.exception("laco do descanso de tela morreu")

    async def encerrar(self) -> None:
        if self._tarefa is not None:
            self._tarefa.cancel()
            try:
                await self._tarefa
            except asyncio.CancelledError:
                pass
            self._tarefa = None
        # Deixa a tela ACESA ao sair. Se o servico caiu, ninguem mais vai
        # acende-la, e um monitor preto e indistinguivel de um robo morto.
        await self.acordar("encerrando")
