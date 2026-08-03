"""Quem esta falando com o robo agora - liga o rosto reconhecido a conversa.

## O buraco que isto fecha

As 152 conversas gravadas no banco tem `pessoa_id = NULL`. Todas. Nao por
falta de coluna nem de reconhecimento facial: a visao reconhece o rosto e
publica `vision.person_recognized` com o nome, a tabela `pessoas` tem o id, e
a saudacao ja consulta os dois. So que `MissionPlanner.processar()` recebe
`pessoa_id` como PARAMETRO e quem chama (`conversar_fofao.py`) nunca preenche.

O efeito e sutil e caro: a memoria do robo e **da casa**, nao de cada pessoa.
Ele guarda "gosta de cafe forte" sem saber de quem, e depois serve esse fato
para qualquer um. Com tres pessoas na casa - Joao Paulo, Kamal e Marah - isso
mistura o dono, a cuidadora e uma crianca de 3 anos no mesmo balaio.

## Atribuir errado e PIOR que nao atribuir

Esta e a regra que governa o desenho todo. Um fato sem dono e um fato que se
perde; um fato com o dono ERRADO e o robo afirmando algo falso sobre alguem da
familia - e a familia confia no que ele diz. Mesma escolha do extrator de
fatos: preferir perder a inventar.

Por isso a atribuicao **vence por tempo**. Passados `validade_s` sem reconhecer
ninguem, volta a `None` (a memoria da casa, que e o comportamento de hoje) em
vez de continuar creditando a ultima pessoa vista. O risco que sobra e alguem
falar dentro da janela sem ser reconhecido; a janela e curta justamente para
limitar isso.

## Por que nao usa `vision.person_lost`

A visao perde a pessoa a toda hora enquanto ela fala - vira o rosto, sai do
quadro, o rastreador pisca. Zerar a cada perda deixaria a atribuicao inutil na
pratica. O relogio e mais estavel e erra para o lado seguro.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

logger = logging.getLogger("orion.mission.quem_fala")

#: Quanto tempo um reconhecimento continua valendo para creditar a conversa.
#:
#: Sessenta segundos cobre bem uma conversa em que a pessoa vira o rosto ou sai
#: do quadro por alguns instantes, e e curto o bastante para nao atravessar
#: duas visitas diferentes. Mais que o `VALIDADE_VISAO_S` (15 s) do retrato do
#: mundo de proposito: la a pergunta e "tem alguem na minha frente AGORA?";
#: aqui e "quem esta conversando comigo nesta sessao?".
VALIDADE_S = 60.0


class QuemEstaFalando:
    """Guarda quem a visao reconheceu por ultimo, e traduz para `pessoa_id`."""

    def __init__(
        self,
        event_bus,
        memory_client,
        validade_s: float = VALIDADE_S,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self._memoria = memory_client
        self._validade_s = validade_s
        self._relogio = relogio
        self._nome: str | None = None
        self._id: int | None = None
        self._visto_em: float = 0.0
        #: nome -> id. A visao publica reconhecimento a cada quadro; sem este
        #: cache seria uma consulta ao banco do Pi por quadro, atravessando o
        #: link TCP. O elenco de uma casa nao muda no meio de uma conversa.
        self._ids_por_nome: dict[str, int] = {}
        event_bus.subscribe("vision.person_recognized", self._ao_reconhecer)

    async def _ao_reconhecer(self, evento) -> None:
        nome = (evento.dados or {}).get("nome")
        if not nome:
            return  # `person_detected` sem identidade nao credita ninguem
        nome = str(nome).strip()
        if nome != self._nome:
            self._nome = nome
            self._id = await self._resolver_id(nome)
            logger.info("conversa creditada a '%s' (pessoa_id=%s)", nome, self._id)
        self._visto_em = self._relogio()

    async def _resolver_id(self, nome: str) -> int | None:
        if nome in self._ids_por_nome:
            return self._ids_por_nome[nome]
        try:
            pessoas = await self._memoria.recall("pessoas", limite=50) or []
        except Exception:
            # Sem banco a conversa continua - ela so volta a ser da casa.
            logger.warning("nao consegui ler a tabela pessoas", exc_info=True)
            return None
        alvo = nome.casefold()
        for pessoa in pessoas:
            cadastrado = str(pessoa.get("nome", "")).strip()
            # Casa pelo PRIMEIRO nome tambem: a visao devolve o nome cadastrado
            # ("Joao Paulo"), mas nada garante que sera sempre a forma completa.
            if cadastrado.casefold() == alvo or cadastrado.casefold().split(" ")[0] == alvo:
                self._ids_por_nome[nome] = pessoa["id"]
                return pessoa["id"]
        logger.info("'%s' foi reconhecido mas nao esta na tabela pessoas", nome)
        return None

    def pessoa_id(self) -> int | None:
        """Quem creditar por esta fala, ou None se ninguem recente."""
        if self._id is None or self._nome is None:
            return None
        if self._relogio() - self._visto_em > self._validade_s:
            # Venceu: volta a ser conversa da casa. Nao limpa `_nome`/`_id`
            # porque um reconhecimento novo do MESMO nome deve reaproveitar o
            # id sem consultar o banco de novo.
            return None
        return self._id

    @property
    def nome(self) -> str | None:
        """Nome de quem esta falando, ou None. So para log e diagnostico."""
        return self._nome if self.pessoa_id() is not None else None
