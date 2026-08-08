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
        # A JANELA DE ESCUTA. Enquanto o Piper fala, o robo NAO ESCUTA (ver
        # sintese.py): numa aula de doze minutos ele ficaria surdo a aula
        # inteira, e foi o que o dono relatou - "ele tem que parar quando
        # mandar". Este intervalo entre passos e o unico momento em que ele
        # ouve.
        #
        # QUATRO SEGUNDOS, a pedido dele: "tem que ter uma pausa para ouvir,
        # uma pausa superior onde ele va ouvir". Comecou em 0,6 s (so
        # respiro), passou a 1,5 s e ainda era curto - quem quer interromper
        # precisa perceber que a fala parou, decidir e falar. Isso nao cabe
        # em um segundo e meio.
        #
        # O custo e real e vale dizer: 4 s x 12 passos = quase um minuto a
        # mais de aula. E o preco de poder interromper, e o dono escolheu
        # pagar.
        pausa_entre_passos_s: float = 4.0,
        pausa_pergunta_s: float = 6.0,
    ) -> None:
        #: Quanto ele espera depois de uma pergunta de fixacao. Bem maior que
        #: a pausa entre passos, e por isso e um numero separado: a pausa
        #: entre passos e respiro, esta e tempo de PENSAR. Um robo que
        #: pergunta e segue adiante no mesmo segundo nao esta ensinando.
        self._pausa_pergunta_s = pausa_pergunta_s
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
        #: PAUSA. Fica SETADO quando a aula esta correndo - `wait()` so
        #: bloqueia quando alguem pausou. Pedido do dono em 2026-08-07:
        #: *"tem q ter pausar"*.
        #:
        #: Conferida ENTRE os passos, e nao dentro da fala: interromper o
        #: Piper no meio de uma frase corta a palavra pela metade, e uma aula
        #: que para no fim da frase e o que um professor faz quando alguem
        #: levanta a mao.
        self._correndo = asyncio.Event()
        self._correndo.set()
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

            # ABERTURA. Dizer para onde se vai antes de sair andando: quem
            # sabe o que vai aprender presta atencao no que importa. Foi a
            # falta disto que fez a primeira aula soar "rapida e mal
            # explicada" - ela comecava no meio.
            if aula.plano.objetivo:
                itens = [f"Objetivo - {aula.plano.objetivo}"]
                if aula.plano.pre_requisitos:
                    itens.append("Voce precisa saber - " + ", ".join(aula.plano.pre_requisitos))
                if aula.plano.duracao_min:
                    itens.append(f"Duracao - cerca de {aula.plano.duracao_min} minutos")
                await self._quadro.escrever("topicos", texto=f"Plano da aula - {aula.titulo}",
                                            itens=itens)
                await self._falar(
                    f"Antes de comecar, o plano de hoje. {aula.plano.objetivo}."
                )
                await asyncio.sleep(self._pausa)

            # AVISA QUE A JANELA EXISTE. Enquanto eu falo eu nao escuto, e
            # quem nao sabe disso fica repetindo "para" por cima da minha voz
            # achando que o robo o esta ignorando - foi o que aconteceu.
            # Dizer onde falar transforma um defeito aparente numa regra
            # combinada.
            await self._falar(
                "Voce pode me interromper nas pausas entre um trecho e outro. "
                "E so falar: para, ou pausa."
            )
            await asyncio.sleep(self._pausa)

            for numero, passo in enumerate(aula.passos, start=1):
                # Segura aqui se alguem pausou. Fica esperando indefinidamente
                # - quem pausou e quem decide voltar. Uma pausa com prazo
                # seria o robo retomando a aula sozinho no meio de outra
                # conversa.
                await self._correndo.wait()
                # O pulso segura a tela acesa mesmo num passo so de fala.
                await self._bus.publish(
                    TOPICO_AULA, {"aula": aula.id, "passo": numero, "de": aula.quantos_passos}
                )
                if passo.bloco:
                    campos = {k: v for k, v in passo.bloco.items() if k != "tipo"}
                    await self._quadro.escrever(passo.bloco["tipo"], **campos)
                await self._falar(passo.fala)
                await asyncio.sleep(self._pausa)

            # FIXACAO e FECHAMENTO. A pergunta vem com uma pausa DE VERDADE
            # depois: ela existe para a pessoa pensar, e um robo que pergunta
            # e responde sozinho no segundo seguinte nao esta ensinando.
            if aula.plano.fixacao:
                await self._bus.publish(TOPICO_AULA, {"aula": aula.id, "passo": "fixacao"})
                await self._quadro.escrever(
                    "topicos", texto="Para voce responder", itens=list(aula.plano.fixacao)
                )
                await self._falar("Agora responda para mim, com as suas palavras.")
                for pergunta in aula.plano.fixacao:
                    await self._falar(pergunta)
                    await asyncio.sleep(self._pausa_pergunta_s)

            if aula.plano.fechamento:
                await self._quadro.escrever("texto", texto=aula.plano.fechamento)
                await self._falar(aula.plano.fechamento)
            if aula.fonte:
                await self._falar(f"O conteudo desta aula veio de {aula.fonte}.")
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

    @property
    def pausado(self) -> bool:
        return self.dando_aula and not self._correndo.is_set()

    def pausar(self) -> bool:
        """Segura a aula no fim do passo atual. Devolve se havia aula."""
        if not self.dando_aula:
            return False
        self._correndo.clear()
        logger.info("aula pausada")
        return True

    def retomar(self) -> bool:
        """Continua de onde parou. Devolve se havia aula pausada."""
        if not self.pausado:
            return False
        self._correndo.set()
        logger.info("aula retomada")
        return True

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
