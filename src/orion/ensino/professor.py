"""O PROFESSOR - quem conduz a aula, sem chamar modelo nenhum.

Le a aula do banco (`banco_aulas.py`), escreve no quadro e fala. Um passo de
cada vez, e o passo seguinte so comeca quando o anterior terminou de ser
FALADO: e o que faz virar aula em vez de slide passando sozinho.

## O quadro vem ANTES da fala, e isso e uma decisao

A pessoa le em um segundo e ouve em dez. Escrevendo primeiro, ela ja esta
lendo enquanto ele explica - que e o que um professor faz de verdade ao
escrever no quadro enquanto fala. Ao contrario, ela ouviria a explicacao de
algo que ainda nao esta escrito.

## Quem interrompe

Uma aula longa que nao para nao e um professor, e um alto-falante. `parar()`
cancela na hora, e a palavra "para" ja tem caminho proprio no Mission
Planner, sem depender de internet nem de modelo.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from orion.display.quadro import Quadro
from orion.ensino.banco_aulas import Aula, BancoAulas

logger = logging.getLogger("orion.ensino.professor")

#: Topico do pulso da aula. Existe por causa do descanso de tela: um trecho
#: em que o robo explica sem escrever passaria dos 3 minutos e a tela
#: apagaria no meio da aula. Ver `display/tela.py` - so a aula adia o
#: descanso, e e por isso que este topico e dela e nao da voz.
TOPICO_AULA = "display.aula"


class Professor:
    def __init__(
        self,
        banco: BancoAulas,
        quadro: Quadro,
        falar: Callable[[str], Awaitable[None]],
        event_bus,
        pausa_entre_passos_s: float = 0.6,
    ) -> None:
        self._banco = banco
        self._quadro = quadro
        self._falar = falar
        self._bus = event_bus
        self._pausa = pausa_entre_passos_s
        self._tarefa: asyncio.Task | None = None
        self._aula_atual: Aula | None = None
        #: Fica LIMPO enquanto o robo esta falando. A aula nao pode comecar
        #: enquanto a frase do turno ("vou dar uma aula de ingles") ainda
        #: esta sendo dita: duas falas ao mesmo tempo ja mataram este
        #: processo com SEGV, dez vezes em sete dias, e sem traceback.
        self._silencio = asyncio.Event()
        self._silencio.set()
        event_bus.subscribe("voice.response_started", self._marcar_falando)
        event_bus.subscribe("voice.response_finished", self._marcar_calado)

    async def _marcar_falando(self, evento) -> None:
        self._silencio.clear()

    async def _marcar_calado(self, evento) -> None:
        self._silencio.set()

    async def _esperar_silencio(self, limite_s: float = 25.0) -> None:
        """Espera o robo parar de falar - com teto.

        O teto existe porque `voice.response_finished` pode nunca chegar (o
        turno falhou, o audio travou), e uma aula que nunca comeca e pior que
        uma aula que comeca por cima de meia frase.
        """
        try:
            await asyncio.wait_for(self._silencio.wait(), timeout=limite_s)
        except TimeoutError:
            logger.warning("comecando a aula sem o fim da fala anterior (%.0fs)", limite_s)

    @property
    def dando_aula(self) -> bool:
        return self._tarefa is not None and not self._tarefa.done()

    @property
    def aula_atual(self) -> Aula | None:
        return self._aula_atual

    def procurar(self, pedido: str, nivel: str | None = None) -> Aula | None:
        """Acha a aula que atende ao pedido. None = nao tem no banco.

        None NAO e falha: e o primeiro degrau da cascata que o dono desenhou
        - o banco responde quando sabe, e quando nao sabe a conversa segue
        para a internet e para a IA. Devolver "quase isso" seria pior que
        nao achar, porque ninguem descobriria que foi um chute.
        """
        achados = self._banco.buscar(pedido, nivel=nivel, limite=1)
        if not achados:
            # Sem aula naquele nivel? Vale oferecer outro nivel do mesmo
            # assunto antes de desistir - quem pediu "avancado" prefere a
            # basica a ouvir "nao tenho".
            achados = self._banco.buscar(pedido, nivel=None, limite=1)
        if not achados:
            return None
        return self._banco.carregar(achados[0].id)

    def oferecer_o_que_tem(self) -> str:
        """O que dizer quando pedem uma aula que ele nao tem.

        Ele sabe QUE nao tem e sabe o que tem - as duas coisas saem do banco,
        sem modelo. Dizer so "nao tenho" seria jogar fora metade do que ele
        sabe da propria estante.
        """
        try:
            aulas = self._banco.listar()
        except Exception:
            logger.exception("falha ao listar as aulas do banco")
            aulas = []
        if not aulas:
            return "Ainda nao tenho nenhuma aula guardada."
        materias = sorted({aula.materia for aula in aulas})
        if len(materias) == 1:
            lista = materias[0]
        else:
            lista = ", ".join(materias[:-1]) + f" e {materias[-1]}"
        return f"Dessa eu ainda nao tenho aula. Por enquanto eu sei dar de {lista}."

    async def dar_aula(self, aula: Aula) -> None:
        """Comeca a aula em segundo plano e volta na hora.

        Em segundo plano porque quem chama e o turno da conversa: segurar o
        turno ate o fim da aula deixaria o robo surdo por dez minutos - ele
        nao ouviria nem "para".
        """
        await self.parar()
        self._aula_atual = aula
        self._tarefa = asyncio.create_task(self._conduzir(aula))

    async def _conduzir(self, aula: Aula) -> None:
        logger.info("aula: %s (%s, %d passos)", aula.id, aula.nivel, aula.quantos_passos)
        try:
            # A frase de abertura ainda esta saindo pelo alto-falante.
            await self._esperar_silencio()
            await self._quadro.limpar()
            for numero, passo in enumerate(aula.passos, start=1):
                # O pulso segura a tela acesa mesmo num passo so de fala.
                await self._bus.publish(
                    TOPICO_AULA, {"aula": aula.id, "passo": numero, "de": aula.quantos_passos}
                )
                if passo.bloco:
                    campos = {k: v for k, v in passo.bloco.items() if k != "tipo"}
                    await self._quadro.escrever(passo.bloco["tipo"], **campos)
                await self._falar(passo.fala)
                await asyncio.sleep(self._pausa)
        except asyncio.CancelledError:
            logger.info("aula %s interrompida", aula.id)
            raise
        except Exception:
            # A aula cai, o robo NAO cai. E ele diz que caiu: o modo de falha
            # que este projeto mais repete e a coisa parar sem ninguem saber.
            logger.exception("aula %s falhou no meio", aula.id)
            try:
                await self._falar("Desculpe, me perdi no meio da aula.")
            except Exception:
                logger.exception("falha ao avisar que a aula caiu")
        else:
            logger.info("aula %s terminada", aula.id)
        finally:
            self._aula_atual = None

    async def parar(self) -> None:
        """Interrompe a aula em andamento. Silencioso se nao houver nenhuma."""
        if self._tarefa is None or self._tarefa.done():
            self._tarefa = None
            return
        self._tarefa.cancel()
        try:
            await self._tarefa
        except asyncio.CancelledError:
            pass
        self._tarefa = None
        self._aula_atual = None
