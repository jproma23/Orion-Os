"""Saudação espontânea: o Fofão fala primeiro ao reconhecer alguém.

Pedido do dono (2026-07-29): *"quero que ele fale tudo"*, *"praticamente ao me
ver me cumprimentar"*, *"agir espontaneamente como estivesse vendo o seu
projetista e criador"*.

POR QUE ISTO SÓ FOI POSSÍVEL HOJE: até esta data o robô não tinha como saber
QUEM estava na frente dele. O `vision.person_recognized` - o único evento que
carrega o nome - era publicado no Notebook e não chegava a lugar nenhum: o
diário lia o nome do evento errado, o `EstadoDoMundo` também, e o FOLLOW
esperava por ele sem que ninguém o encaminhasse. Ele via o dono todos os dias
e registrava "vi uma pessoa que não reconheci". Cumprimentar era impossível
por falta de identidade, não por falta de voz.

O CUIDADO QUE DEFINE ESTE MÓDULO: `vision.person_recognized` sai uma vez por
QUADRO rastreado - até 15 vezes por segundo. Falar a cada um seria um robô
gritando o seu nome sem parar. Aqui, reconhecer é diferente de CHEGAR: só a
chegada merece um cumprimento.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable

from orion.kernel.event_bus import EventBus, Evento
from orion.mission.acoes_faladas import sem_marcador as _sem_marcador

logger = logging.getLogger("orion.mission.saudacao")

TOPICO_RECONHECIDA = "vision.person_recognized"
TOPICO_PERDIDA = "vision.person_lost"
TOPICO_ACORDOU = "voice.wake_detected"
TOPICO_RESPOSTA_FIM = "voice.response_finished"
TOPICO_SAUDACAO = "voice.saudacao"

#: Tempo sem ver a pessoa para que revê-la conte como CHEGADA de novo.
#: Meia hora: sair da sala e voltar em dois minutos não é chegar - e um robô
#: que cumprimenta a cada passagem cansa em uma tarde.
INTERVALO_ENTRE_SAUDACOES_S = 1800.0

#: Depois de uma conversa, por quanto tempo ninguém "chega".
#:
#: Queixa do dono (2026-08-09): *"no meio de uma conversa ele me sauda e diz q
#: é um prazer me ver sendo q ja estou falando com ele algum tempo"*. O
#: `_conversando` cobria só o turno - de "Fofão" até a resposta terminar - e
#: entre um turno e outro a pessoa voltava a ser alguém que acabou de aparecer.
#:
#: Quinze minutos: uma conversa tem pausas de vários minutos e continua sendo a
#: mesma conversa. Quem está falando com o robô não chegou agora - está aqui
#: desde antes, e o robô sabe disso melhor que a câmera.
JANELA_DE_CONVERSA_S = 900.0

#: Variações para não soar gravado. A escolha é aleatória de propósito: a
#: mesma frase toda vez faz o robô parecer um alarme, não um morador.
FRASES = (
    "Oi, {nome}!",
    "Olá, {nome}! Que bom te ver.",
    "{nome}! Você por aqui.",
    "Oi, {nome}, tudo bem?",
    "Opa, {nome}! Bom te ver de novo.",
)

#: Cumprimento por RELAÇÃO, definida pelo dono em `config/orion.yaml`
#: (seção `familia`). Ele não trata todo mundo igual porque as pessoas da casa
#: não são iguais para ele: um é quem o construiu, outro é uma criança de 3
#: anos, outra é quem cuida da casa.
#:
#: Relação sem entrada aqui cai em FRASES - genérico funciona, só é menos
#: pessoal. Nunca falha por falta de correspondência.
FRASES_POR_RELACAO = {
    "criador": (
        "Oi, {nome}! Que bom te ver, meu criador.",
        "{nome}! Você voltou. Estava aqui de olho na casa.",
        "Olá, {nome}! Pronto para o que você precisar.",
    ),
    "irmao": (
        "Oi, {nome}!",
        "{nome}! Vamos brincar?",
        "Olha o {nome} chegando!",
    ),
    "mae": (
        "Olá, {nome}!",
        "Oi, {nome}, tudo bem por aí?",
        "{nome}! Está tudo em ordem por aqui.",
    ),
}


class SaudacaoEspontanea:
    """Cumprimenta pelo nome quando alguém conhecido CHEGA.

    Roda no Notebook, junto com a voz e a visão - é lá que estão o
    reconhecimento facial e o sintetizador.
    """

    #: Quanto se espera a IA compor antes de usar a frase de reserva.
    TIMEOUT_COMPOR_S = 8.0
    #: Cumprimento e uma frase, nao um discurso.
    MAX_CARACTERES = 160
    #: Quantas frases anteriores o compositor recebe como "nao repita estas".
    #: Tres e o bastante para forcar palavra nova sem encher o prompt - e sem
    #: pedir o impossivel, que seria nunca repetir nada.
    MAX_FRASES_LEMBRADAS = 3

    def __init__(
        self,
        event_bus: EventBus,
        falar: Callable[[str], Awaitable[None]],
        intervalo_entre_saudacoes_s: float = INTERVALO_ENTRE_SAUDACOES_S,
        janela_de_conversa_s: float = JANELA_DE_CONVERSA_S,
        agora: Callable[[], float] | None = None,
        escolher_frase: Callable[[tuple[str, ...]], str] | None = None,
        familia: list[dict] | None = None,
        compor=None,
        relogio: Callable[[], time.struct_time] | None = None,
        observacoes: Callable[[], Awaitable[list[dict]]] | None = None,
        recados=None,
    ) -> None:
        self._bus = event_bus
        self._falar = falar
        #: QUEM ENTREGA OS RECADOS (2026-08-03). A entrega mora aqui e nao num
        #: modulo proprio porque este ja e o unico lugar do sistema que sabe
        #: distinguir "estou vendo a Mara" (ate 15 vezes por segundo) de "a
        #: Mara CHEGOU" - e "quando a vir" quer dizer chegou, nao apareceu no
        #: quadro. Um vigia separado teria que reimplementar essa distincao, e
        #: entao haveria duas ideias de chegada no mesmo robo.
        #:
        #: Opcional como todo o resto: sem banco, o robo cumprimenta e nao
        #: entrega, em vez de nao cumprimentar.
        self._recados = recados
        #: nome -> relação, vinda do orion.yaml. Nome fora daqui recebe o
        #: cumprimento genérico em vez de nenhum.
        self._relacao_de = {
            str(p.get("nome")): str(p.get("relacao", "")) for p in (familia or [])
        }
        #: nome -> como o dono descreveu essa pessoa no orion.yaml
        self._descricao_de = {
            str(p.get("nome")): str(p.get("descricao", "")) for p in (familia or [])
        }
        #: Quem ESCREVE o cumprimento. `None` = sorteia da lista fixa.
        #:
        #: Pedido do dono (2026-07-29): *"deixa a conversa mais natural"*,
        #: *"deixa a IA ver as palavras que melhor se adaptem ao contexto"*.
        #: Uma lista fixa nunca soa como alguem falando - soa como um alarme
        #: com sinonimos.
        #:
        #: A lista continua existindo e NAO e decoracao: e a reserva. Se a IA
        #: demorar, falhar ou a internet cair, o robo cumprimenta assim mesmo.
        #: Um cumprimento comum e melhor que silencio.
        self._compor = compor
        self._intervalo_s = intervalo_entre_saudacoes_s
        self._agora = agora or time.monotonic
        #: Relógio de PAREDE, separado do `_agora` monotônico de propósito: o
        #: monotônico serve para medir intervalos e não sabe que horas são.
        self._relogio = relogio or time.localtime
        #: O que ele já viu hoje (diário). Opcional - sem isto o cumprimento
        #: perde a parte mais rica do contexto, mas continua funcionando.
        self._observacoes = observacoes
        self._escolher = escolher_frase or random.choice
        #: nome -> instante do último cumprimento
        self._cumprimentados: dict[str, float] = {}
        #: nome -> as últimas frases já ditas àquela pessoa.
        #:
        #: Existe porque as duas exigências do dono brigam entre si: *"não
        #: relate o horário"* e *"não repita as frases"*. Sem poder falar de
        #: nada concreto, o modelo só pode variar a escolha de palavras numa
        #: tarefa de seis palavras - e aí ele converge. Medido em 2026-07-29:
        #: cinco chamadas seguidas deram "Que bom te ver" quatro vezes.
        #:
        #: Dizer a ele o que JÁ FOI DITO força palavra nova sem que nenhum
        #: fato precise ser recitado. Só isto ataca a repetição sem
        #: reintroduzir o relatório de dados.
        self._frases_usadas: dict[str, list[str]] = {}
        #: True entre "Fofão" e o fim da resposta - não se fala por cima de
        #: uma conversa em andamento. Cumprimentar alguém no meio da própria
        #: frase é pior do que não cumprimentar.
        self._conversando = False
        #: Quando o último turno de conversa aconteceu. `_conversando` sozinho
        #: só cobria o turno; entre um turno e outro a pessoa voltava a "chegar"
        #: e ouvia "que bom te ver" no meio do papo (dono, 09/08).
        self._ultima_conversa: float | None = None
        self._janela_de_conversa_s = janela_de_conversa_s
        #: Cumprimento em voo - ver `_ao_reconhecer`.
        self._tarefa_cumprimento: asyncio.Task | None = None

        event_bus.subscribe(TOPICO_RECONHECIDA, self._ao_reconhecer)
        event_bus.subscribe(TOPICO_ACORDOU, self._ao_acordar)
        event_bus.subscribe(TOPICO_RESPOSTA_FIM, self._ao_terminar_resposta)

    async def _ao_acordar(self, evento: Evento) -> None:
        self._conversando = True
        self._ultima_conversa = self._agora()

    async def _ao_terminar_resposta(self, evento: Evento) -> None:
        self._conversando = False
        # Marcado nas DUAS pontas: o wake abre a janela na hora, e o fim da
        # resposta a renova. Marcar só no wake faria a janela começar a correr
        # enquanto o robô ainda estava falando.
        self._ultima_conversa = self._agora()

    def _conversa_em_andamento(self) -> bool:
        """Alguém está falando com ele agora, ou acabou de falar.

        Quem conversa não CHEGA - já estava aqui. Esta é a diferença que o
        `_conversando` sozinho não via, porque ele só valia dentro do turno.
        """
        if self._conversando:
            return True
        if self._ultima_conversa is None:
            return False
        return self._agora() - self._ultima_conversa < self._janela_de_conversa_s

    async def _ao_reconhecer(self, evento: Evento) -> None:
        nome = evento.dados.get("nome")
        if not nome:
            return  # sem identidade não há a quem cumprimentar pelo nome
        if self._conversa_em_andamento():
            # NAO marca `_cumprimentados`: a pessoa nao foi cumprimentada, so
            # nao era hora. Marcar aqui adiaria o cumprimento de verdade em
            # meia hora a cada quadro em que ela aparecesse durante a conversa.
            return
        # O instante da ULTIMA vez tem que ser lido AGORA, porque
        # `_chegou_agora` grava o horario de hoje em cima dele. Sem isto o
        # contexto dizia "ha 0 min" toda vez e nunca "primeira vez" - foi o
        # proprio teste novo que pegou, e era o mesmo bug de mesmice que este
        # commit corrige, so uma camada abaixo.
        anterior = self._cumprimentados.get(nome)
        if not self._chegou_agora(nome):
            return

        # FORA DO LACO DA VISAO, de proposito. Este handler e chamado pelo
        # Event Bus dentro do pipeline da camera; compor com a IA leva
        # segundos e falar leva mais alguns. Fazer isso aqui dentro travaria o
        # rastreamento - e perder a pessoa de vista para cumprimenta-la e um
        # negocio ruim.
        # Referencia guardada (varredura de 2026-08-04): sem ela a tarefa
        # pode ser coletada no meio - o robo reconheceria a pessoa, comecaria
        # a compor a frase e simplesmente nao falaria, sem erro nenhum. Uma
        # saudacao por vez ja e a regra de fato (o `_chegou_agora` barra a
        # repeticao), entao um unico campo basta.
        self._tarefa_cumprimento = asyncio.create_task(self._cumprimentar(nome, anterior))

    async def _cumprimentar(self, nome: str, anterior: float | None = None) -> None:
        frase = await self._escrever_frase(nome, anterior)
        logger.info("saudação espontânea para %s: %r", nome, frase)
        self._lembrar_frase(nome, frase)
        # Publica ANTES de falar: o avatar reage ao evento, e a fala bloqueia
        # por segundos. Se publicasse depois, a legenda apareceria quando a
        # voz já tivesse terminado.
        await self._bus.publish(TOPICO_SAUDACAO, {"nome": nome, "texto": frase})
        try:
            await self._falar(frase)
        except Exception:
            # Falha na voz não pode derrubar a visão (Cap 6 s.8). Um
            # cumprimento perdido é irrelevante perto de perder o alvo.
            logger.warning("não consegui falar o cumprimento para %s", nome, exc_info=True)
            # Sem voz não há como entregar recado nenhum, e marcá-lo como
            # entregue seria dar por cumprida uma promessa que ninguém ouviu.
            return

        await self._entregar_recados(nome)

    async def _entregar_recados(self, nome: str) -> None:
        """O que faltava quando a Mara chegou (2026-08-03).

        DEPOIS do cumprimento, e não no lugar dele: chegar em casa e ouvir um
        recado antes de um "oi" é o robô tratando a pessoa como caixa de
        entrada. E depois de FALAR, não em paralelo - as duas falas sairiam
        por cima uma da outra no mesmo alto-falante.

        Marcar como entregue só DEPOIS de a fala terminar. Se a voz falhar no
        meio, o recado continua pendente e volta na próxima chegada: repetir
        um recado é chato, perder um é quebrar a promessa.
        """
        if self._recados is None:
            return
        try:
            pendentes = await self._recados.pendentes(nome)
        except Exception:
            logger.warning("não consegui consultar recados de %s", nome, exc_info=True)
            return

        for recado in pendentes:
            texto = str(recado.get("texto", "")).strip()
            if not texto:
                continue
            try:
                await self._falar(texto)
            except Exception:
                logger.warning("não consegui falar o recado para %s", nome, exc_info=True)
                return
            logger.info("recado entregue a %s: %r", nome, texto)
            await self._recados.marcar_entregue(recado["id"])

    async def _contexto_do_momento(self, nome: str, anterior: float | None) -> str:
        """O que muda de um cumprimento para o outro.

        BUG REAL, relatado pelo dono ao vivo (2026-07-29): *"ele só fala 3
        frases repetidas"*. A IA ESTAVA compondo - o log prova. O problema é
        que o prompt só tinha nome e descrição, que são os mesmos toda vez.
        Prompt igual, tarefa mínima e temperatura baixa produzem a mesma
        saída: 'Olá, João Paulo! Que bom te ver (por) aqui!' três vezes
        seguidas. Pedir variedade sem dar sobre o que variar não funciona.

        Nada aqui é decisão: são fatos do momento. A IA escolhe as palavras.
        Falha em qualquer parte nunca impede o cumprimento (Cap 6 s.8).
        """
        agora_local = self._relogio()
        hora = agora_local.tm_hour
        if hora < 5:
            momento = "madrugada"
        elif hora < 12:
            momento = "manhã"
        elif hora < 18:
            momento = "tarde"
        else:
            momento = "noite"
        # SÓ o momento do dia, sem o relógio. O dono ouviu o cumprimento e
        # cortou na hora (2026-07-29): *"ele relatou o horário, não precisa
        # disso"*. Tinha razão - a hora exata no prompt convida o modelo a
        # LER a hora em voz alta, e ninguém pede a hora ao ser cumprimentado.
        #
        # O contexto existe para ele VARIAR as palavras, não para narrar
        # dados. Um fato que ele recita em voz alta virou ruído, não contexto.
        # "é noite" muda o tom ("boa noite", mais calmo) sem dar um número
        # para recitar; a hora exata não acrescentava tom nenhum.
        partes = [f"é {momento}"]

        if anterior is None:
            partes.append(f"é a primeira vez que você vê {nome} desde que ligou")
        else:
            minutos = max(0, int((self._agora() - anterior) // 60))
            if minutos < 60:
                partes.append(f"você falou com {nome} há {minutos} min")
            else:
                partes.append(f"você falou com {nome} há cerca de {minutos // 60} h")

        if self._observacoes is not None:
            try:
                vistos = await self._observacoes()
            except Exception:
                # o diário é enfeite aqui, não requisito: sem ele o
                # cumprimento sai com menos contexto, e nada mais.
                logger.debug("diário indisponível para o cumprimento", exc_info=True)
                vistos = []
            if vistos:
                resumo = "; ".join(
                    f"{o.get('quando', '?')} {o.get('o_que', '?')}" for o in vistos[-4:]
                )
                partes.append(f"hoje você já viu: {resumo}")
            else:
                partes.append("você ainda não viu ninguém hoje")

        return "\n".join(f"- {p}" for p in partes)

    def _lembrar_frase(self, nome: str, frase: str) -> None:
        """Guarda o que foi dito, para não ser dito de novo na próxima vez.

        Guarda o que SAIU de fato - venha da IA ou da lista de reserva. O que
        importa é o que a pessoa ouviu, não de onde veio.
        """
        usadas = self._frases_usadas.setdefault(nome, [])
        usadas.append(frase)
        del usadas[: -self.MAX_FRASES_LEMBRADAS]

    def _frase_da_lista(self, nome: str) -> str:
        relacao = self._relacao_de.get(nome, "")
        return self._escolher(FRASES_POR_RELACAO.get(relacao, FRASES)).format(nome=nome)

    async def _escrever_frase(self, nome: str, anterior: float | None = None) -> str:
        """A IA compõe; a lista fixa é a rede embaixo.

        NUNCA levanta: qualquer falha - rede fora, chave vencida, modelo
        lento, resposta vazia - cai na lista. O robô cumprimentar de forma
        comum é infinitamente melhor que ficar mudo porque a internet caiu.
        """
        if self._compor is None:
            return self._frase_da_lista(nome)
        try:
            frase = await asyncio.wait_for(
                self._compor(
                    nome,
                    self._descricao_de.get(nome, ""),
                    await self._contexto_do_momento(nome, anterior),
                    list(self._frases_usadas.get(nome, ())),
                ),
                timeout=self.TIMEOUT_COMPOR_S,
            )
        except Exception:
            logger.info("IA não compôs o cumprimento - usando a frase de reserva", exc_info=True)
            return self._frase_da_lista(nome)
        # O MARCADOR SAI AQUI. Quem compõe é o `ai_manager`, e o prompt dele
        # carrega o catálogo de ações - então o modelo às vezes abre o
        # cumprimento com `#acao:...`. Sem este passo isso ia inteiro para o
        # alto-falante: em 09/08 o robô disse em voz alta "#acao:girar_cabeca
        # Olá, criador!" duas vezes, e `girar_cabeca` nem existe no catálogo.
        #
        # `sem_marcador` e nao `extrair`: aqui não há ação para carregar
        # argumento, então o resto da linha É a frase. O `extrair` a levaria
        # embora junto com o marcador e o robô cairia na frase de reserva
        # toda vez que o modelo escrevesse a tag.
        #
        # A ação é DESCARTADA de propósito: o próprio prompt do cumprimento
        # diz "não ofereça nem prometa nenhuma ação; só cumprimente" -
        # cumprimentar não é hora de mexer o corpo.
        frase = _sem_marcador(frase or "").strip()
        if not frase:
            return self._frase_da_lista(nome)
        # Teto de tamanho: cumprimento e uma frase, nao um discurso. Sem isto
        # um modelo prolixo faria o robo falar por 30s toda vez que alguem
        # entrasse na sala.
        return frase[: self.MAX_CARACTERES]

    def _chegou_agora(self, nome: str) -> bool:
        """True só quando revê-la conta como uma CHEGADA, não continuação.

        Este é o coração do módulo: `vision.person_recognized` chega até 15
        vezes por segundo enquanto a pessoa estiver no quadro. Sem esta janela
        o robô repetiria o nome sem parar.
        """
        agora = self._agora()
        anterior = self._cumprimentados.get(nome)
        if anterior is not None and agora - anterior < self._intervalo_s:
            return False
        self._cumprimentados[nome] = agora
        return True
