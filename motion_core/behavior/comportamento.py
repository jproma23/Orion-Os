"""Comportamento: a unidade plugável do Behavior Core (EDR-0020).

Cada comportamento do Jarvis (atender, vigiar, patrulhar, repousar...) é uma
subclasse desta. O maestro (BehaviorCore) escolhe, a cada instante, o
comportamento de MAIOR prioridade que "quer rodar" e o coloca no controle.
"""
from __future__ import annotations

import inspect

from orion.kernel.event_bus import EventBus

#: Vale nos dois modos de controle - é o padrão de todo comportamento que não
#: disputa o volante (voz, gesto, vigília, repouso, e a segurança reativa).
AUTORIDADE_SEMPRE = "sempre"
#: Só quando o operador está no volante (o joystick do dashboard).
AUTORIDADE_MANUAL = "manual"
#: Só quando o robô está no volante (a IA estratégica e a ronda).
AUTORIDADE_AUTONOMA = "autonoma"


class Comportamento:
    """Base de um comportamento arbitrado pelo maestro.

    `prioridade`: MAIOR número = mais importante (vence a arbitragem). Segue
    a escada do EDR-0020 (100 segurança ... 10 repouso).

    `autoridade`: de QUEM este comportamento é a mão. A prioridade resolve
    "quem é mais urgente"; ela não resolve "quem está no volante" - e tratar
    as duas perguntas como uma só foi um defeito real, medido em 2026-07-30:
    a IA decidiu `explorar` 5444 vezes e o robô nunca saiu do lugar, porque
    quem tinha prioridade maior escrevia por cima da decisão dela sem que
    nada denunciasse. Quem está no volante é uma ESCOLHA do dono, feita pelo
    botão do dashboard, não uma consequência da tabela de prioridades.

    O padrão é `AUTORIDADE_SEMPRE`: declarar nada mantém o comportamento
    ativo nos dois modos, que é o certo para segurança, voz e gesto.
    """

    nome: str = "comportamento"
    prioridade: int = 0
    autoridade: str = AUTORIDADE_SEMPRE

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        #: preenchido pelo BehaviorCore ao registrar - permite a subclasse
        #: acordar o maestro quando seu gatilho muda (self._reavaliar()).
        self._maestro = None

    def _reavaliar(self) -> None:
        """Pede ao maestro para reavaliar quem deve estar no controle.
        Chame quando `quer_rodar()` mudar (ex.: chegou um evento gatilho)."""
        if self._maestro is not None:
            self._maestro.pedir_reavaliacao()

    def quer_rodar(self) -> bool:
        """True quando este comportamento deseja o controle AGORA.

        A subclasse liga isso ao seu gatilho (ex.: ouviu "Jarvis", detectou
        rosto desconhecido, chegou o horário da ronda). Quando o valor muda,
        a subclasse deve chamar `maestro.pedir_reavaliacao()` para o maestro
        reagir na hora, sem esperar o próximo tick.
        """
        return False

    async def executar(self) -> None:
        """Faz o trabalho do comportamento enquanto estiver no controle.

        Pode ser **cancelada** a qualquer momento se um comportamento de
        prioridade maior assumir (preempção). Se precisar limpar algo ao ser
        interrompida, trate `asyncio.CancelledError` e re-levante. Ao voltar
        ao controle depois, `executar` é chamada de novo — cabe à subclasse
        lembrar onde parou, se quiser retomar em vez de recomeçar.
        """
        raise NotImplementedError


async def resolver_contexto(montar) -> str:
    """Chama o montador de contexto, que pode ser sincrono OU assincrono.

    Mora nesta base porque os DOIS consumidores a usam (o maestro e o
    comportamento IaEstrategica) e ela e o unico modulo que os dois ja
    importam - por aqui nao ha ciclo de import.

    POR QUE EXISTE (2026-07-29): o contexto do Pi passou a LER O BANCO, para
    trazer as observacoes do diario, e leitura de banco e `async`. Antes o
    montador era obrigatoriamente sincrono, e era exatamente por isso que o
    diario NUNCA chegava a IA estrategica: nao havia como esperar por ele. Ela
    ouvia "o diario esta VAZIO" todo ciclo, mesmo com o diario gravando -
    porque a unica forma de responder em tempo era nao consultar.

    Aceitar as duas formas mantem validos, sem mudanca nenhuma, todos os
    montadores sincronos que ja existem (e os testes deles).
    """
    if montar is None:
        return ""
    resultado = montar()
    if inspect.isawaitable(resultado):
        resultado = await resultado
    return resultado or ""
