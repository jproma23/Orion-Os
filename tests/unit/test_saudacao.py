"""Testes da saudação espontânea (src/orion/mission/saudacao.py).

O risco que estes testes guardam não é "não cumprimentar" - é cumprimentar
DEMAIS. `vision.person_recognized` sai uma vez por quadro rastreado, até 15
vezes por segundo; sem trava, o robô repetiria o nome da pessoa sem parar.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from orion.kernel.event_bus import EventBus
from orion.mission.saudacao import (
    FRASES,
    FRASES_POR_RELACAO,
    SaudacaoEspontanea,
)

FAMILIA = [
    {"nome": "João Paulo", "relacao": "criador"},
    {"nome": "Kamal", "relacao": "irmao"},
    {"nome": "Marah", "relacao": "mae"},
]


@pytest_asyncio.fixture
async def bus():
    barramento = EventBus()
    tarefa = asyncio.create_task(barramento.iniciar())
    yield barramento
    barramento.parar()
    await tarefa


class _Relogio:
    def __init__(self) -> None:
        self.agora = 1000.0

    def __call__(self) -> float:
        return self.agora

    def avancar(self, segundos: float) -> None:
        self.agora += segundos


def _montar(bus, relogio=None, relogio_parede=None, **kwargs):
    ditas: list[str] = []

    async def falar(texto: str) -> None:
        ditas.append(texto)

    saudacao = SaudacaoEspontanea(
        bus,
        falar,
        agora=relogio or _Relogio(),
        relogio=relogio_parede,
        escolher_frase=lambda frases: frases[0],  # determinístico no teste
        familia=FAMILIA,
        **kwargs,
    )
    return saudacao, ditas


async def _ver(bus, nome: str, vezes: int = 1) -> None:
    for _ in range(vezes):
        await bus.publish("vision.person_recognized", {"pessoa_id": 1, "nome": nome})
    await bus.aguardar_fila_vazia()
    # o cumprimento roda FORA do laco da visao (asyncio.create_task), entao o
    # teste precisa ceder a vez para a task terminar
    for _ in range(6):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cumprimenta_ao_reconhecer(bus):
    _, ditas = _montar(bus)

    await _ver(bus, "João Paulo")

    assert len(ditas) == 1
    assert "João Paulo" in ditas[0]


@pytest.mark.asyncio
async def test_nao_repete_enquanto_a_pessoa_continua_na_frente(bus):
    """O TESTE QUE IMPORTA. O evento chega até 15x/s enquanto a pessoa estiver
    no quadro - sem a janela de silêncio o robô gritaria o nome sem parar."""
    _, ditas = _montar(bus)

    await _ver(bus, "João Paulo", vezes=40)

    assert len(ditas) == 1, f"cumprimentou {len(ditas)} vezes seguidas"


@pytest.mark.asyncio
async def test_cumprimenta_de_novo_depois_de_um_tempo_longe(bus):
    """Contraprova: a trava não pode calar o robô para sempre. Chegar de novo
    meia hora depois é uma chegada nova."""
    relogio = _Relogio()
    _, ditas = _montar(bus, relogio=relogio, intervalo_entre_saudacoes_s=1800.0)

    await _ver(bus, "Marah")
    relogio.avancar(1801.0)
    await _ver(bus, "Marah")

    assert len(ditas) == 2


@pytest.mark.asyncio
async def test_cada_pessoa_tem_sua_propria_janela(bus):
    """Cumprimentar o João Paulo não pode calar o cumprimento da Marah."""
    _, ditas = _montar(bus)

    await _ver(bus, "João Paulo")
    await _ver(bus, "Marah")

    assert len(ditas) == 2


@pytest.mark.asyncio
async def test_fala_diferente_conforme_a_relacao(bus):
    """Definido pelo dono: ele é o criador, Kamal é como um irmão (3 anos),
    Marah é como uma mãe. O robô não trata os três igual."""
    _, ditas = _montar(bus)

    await _ver(bus, "João Paulo")
    await _ver(bus, "Kamal")

    assert ditas[0] == FRASES_POR_RELACAO["criador"][0].format(nome="João Paulo")
    assert ditas[1] == FRASES_POR_RELACAO["irmao"][0].format(nome="Kamal")


@pytest.mark.asyncio
async def test_pessoa_fora_da_familia_recebe_cumprimento_generico(bus):
    """Nome não listado no orion.yaml não pode causar erro nem silêncio."""
    _, ditas = _montar(bus)

    await _ver(bus, "Visita")

    assert ditas == [FRASES[0].format(nome="Visita")]


@pytest.mark.asyncio
async def test_nao_fala_por_cima_de_uma_conversa(bus):
    """Entre "Fofão" e o fim da resposta o robô está conversando. Cumprimentar
    no meio da própria frase é pior do que não cumprimentar - a saudação adia
    para o fim do turno, e não vaza para o turno seguinte."""
    _, ditas = _montar(bus)

    await bus.publish("voice.wake_detected", {})
    await bus.aguardar_fila_vazia()
    await bus.publish("voice.response_started", {})  # o robô começou a falar
    await bus.aguardar_fila_vazia()
    await _ver(bus, "João Paulo")

    assert ditas == []  # no meio da fala: adia, não fala por cima

    await bus.publish("voice.response_finished", {})
    await bus.aguardar_fila_vazia()
    # a chegada pendente saiu assim que o turno acabou
    for _ in range(6):
        await asyncio.sleep(0)
    assert len(ditas) == 1

    # ESTA METADE MUDOU EM 2026-08-09, e a mudanca e o conserto.
    #
    # Antes, terminar a resposta liberava o cumprimento na hora - e era isso
    # que o dono ouvia: *"no meio de uma conversa ele me sauda e diz q é um
    # prazer me ver sendo q ja estou falando com ele algum tempo"*. Quem
    # acabou de conversar nao chegou agora.
    #
    # A janela de 15 min esta em `test_saudacao_na_conversa.py`, junto com a
    # prova de que passado esse tempo a chegada volta a valer.
    await _ver(bus, "João Paulo")

    assert len(ditas) == 1, "cumprimentou de novo logo depois do turno, no meio da conversa"


@pytest.mark.asyncio
async def test_deteccao_sem_nome_nao_cumprimenta(bus):
    """Sem identidade não há a quem cumprimentar pelo nome - e um "oi" para um
    estranho é justamente o que a Sentinela trata, não a saudação."""
    _, ditas = _montar(bus)

    await bus.publish("vision.person_recognized", {"pessoa_id": None, "nome": None})
    await bus.aguardar_fila_vazia()

    assert ditas == []


@pytest.mark.asyncio
async def test_falha_da_voz_nao_derruba_a_visao(bus):
    """Este handler roda dentro do laço da visão (Cap 6 s.8). Um cumprimento
    perdido é irrelevante perto de perder o rastreamento."""
    async def falar_quebrado(texto: str) -> None:
        raise RuntimeError("piper fora do ar")

    SaudacaoEspontanea(
        bus, falar_quebrado, agora=_Relogio(), escolher_frase=lambda f: f[0], familia=FAMILIA
    )

    await _ver(bus, "João Paulo")  # não pode levantar


@pytest.mark.asyncio
async def test_anuncia_no_barramento_para_o_avatar(bus):
    """O avatar precisa saber o que o robô está dizendo, para mostrar a legenda.

    NOTA sobre ordem: `publish` ENFILEIRA, não entrega - o despacho é
    assíncrono. Publicar antes de falar não garante que o assinante rode
    primeiro; garante que o evento já esteja na fila enquanto a fala bloqueia,
    que é o que faz a legenda aparecer junto com a voz em vez de depois dela.
    Uma versão anterior deste teste afirmava a ordem estrita e falhou - a
    asserção estava errada, não o código.
    """
    recebidos: list[dict] = []
    bus.subscribe("voice.saudacao", lambda e: recebidos.append(e.dados))
    _, ditas = _montar(bus)

    await _ver(bus, "João Paulo")

    assert len(recebidos) == 1
    assert recebidos[0]["nome"] == "João Paulo"
    assert recebidos[0]["texto"] == ditas[0], "o avatar mostraria texto diferente do falado"


@pytest.mark.asyncio
async def test_ia_escreve_o_cumprimento(bus):
    """Pedido do dono: "deixa a IA ver as palavras que melhor se adaptem ao
    contexto". Uma lista fixa nao soa como alguem falando."""
    async def compor(nome, descricao, contexto, evitar):
        return f"Que saudade, {nome}! Como foi o seu dia?"

    _, ditas = _montar(bus, compor=compor)
    await _ver(bus, "Joao Paulo")

    assert ditas == ["Que saudade, Joao Paulo! Como foi o seu dia?"]


@pytest.mark.asyncio
async def test_ia_fora_do_ar_cai_na_frase_de_reserva(bus):
    """A rede embaixo. Cumprimentar de forma comum e melhor que ficar mudo
    porque a internet caiu."""
    async def compor(nome, descricao, contexto, evitar):
        raise RuntimeError("sem internet")

    _, ditas = _montar(bus, compor=compor)
    await _ver(bus, "Joao Paulo")

    assert len(ditas) == 1
    assert "Joao Paulo" in ditas[0]


@pytest.mark.asyncio
async def test_resposta_vazia_da_ia_cai_na_reserva(bus):
    async def compor(nome, descricao, contexto, evitar):
        return "   "

    _, ditas = _montar(bus, compor=compor)
    await _ver(bus, "Marah")

    assert len(ditas) == 1 and "Marah" in ditas[0]


@pytest.mark.asyncio
async def test_cumprimento_longo_e_cortado(bus):
    """Cumprimento e uma frase, nao um discurso - sem teto, um modelo prolixo
    faria o robo falar por 30s toda vez que alguem entrasse na sala."""
    async def compor(nome, descricao, contexto, evitar):
        return "ola " * 200

    _, ditas = _montar(bus, compor=compor)
    await _ver(bus, "Kamal")

    assert len(ditas[0]) <= SaudacaoEspontanea.MAX_CARACTERES


# ----- contexto do momento (bug "3 frases repetidas", 2026-07-29) -----
#
# O dono ouviu tres frases se repetindo. A IA ESTAVA compondo; o prompt e que
# nao tinha nada que mudasse entre uma vez e outra. Estes testes guardam
# exatamente isso: que o compositor RECEBE fatos variaveis.


def _hora(h: int, m: int = 0):
    import time as _t
    return lambda: _t.struct_time((2026, 7, 29, h, m, 0, 2, 210, -1))


@pytest.mark.asyncio
async def test_contexto_leva_o_momento_do_dia_mas_NAO_o_relogio(bus):
    """O dono ouviu o cumprimento e cortou: *"ele relatou o horario, nao
    precisa disso"*. A hora exata no prompt convida o modelo a LE-LA em voz
    alta, e ninguem pede a hora ao ser cumprimentado. O momento do dia fica
    (muda o tom); o relogio sai (so daria um numero para recitar)."""
    recebidos = []

    async def compor(nome, descricao, contexto, evitar):
        recebidos.append(contexto)
        return "oi"

    _, _ = _montar(bus, compor=compor, relogio_parede=_hora(21, 5))
    await _ver(bus, "Kamal")

    assert "noite" in recebidos[0]
    assert "21h05" not in recebidos[0], "o relogio nao pode voltar ao prompt"
    assert "21" not in recebidos[0], "nenhum numero de hora no contexto"


@pytest.mark.asyncio
async def test_momento_do_dia_muda_com_a_hora(bus):
    """Se a hora nao virasse texto, o prompt seria o mesmo de manha e de noite
    - e era justamente essa mesmice que produzia a frase repetida."""
    vistos = []

    for hora, esperado in ((3, "madrugada"), (9, "manhã"), (15, "tarde"), (22, "noite")):
        recebidos = []

        async def compor(nome, descricao, contexto, evitar, _r=recebidos):
            _r.append(contexto)
            return "oi"

        _montar(bus, compor=compor, relogio_parede=_hora(hora))
        await _ver(bus, "Marah")
        vistos.append((esperado, recebidos[0]))

    for esperado, contexto in vistos:
        assert esperado in contexto


@pytest.mark.asyncio
async def test_primeira_vez_e_reencontro_dao_contextos_DIFERENTES(bus):
    """O teste que mais importa: dois cumprimentos da MESMA pessoa tem que
    chegar ao compositor com contextos diferentes. Se forem iguais, a IA nao
    tem como variar e a frase repete - o bug relatado."""
    recebidos = []
    relogio = _Relogio()

    async def compor(nome, descricao, contexto, evitar):
        recebidos.append(contexto)
        return f"oi {len(recebidos)}"

    _montar(bus, compor=compor, relogio=relogio, relogio_parede=_hora(10))

    await _ver(bus, "João Paulo")
    relogio.avancar(3600)  # uma hora depois, conta como chegada nova
    await _ver(bus, "João Paulo")

    assert len(recebidos) == 2
    assert "primeira vez" in recebidos[0]
    assert "primeira vez" not in recebidos[1]
    assert recebidos[0] != recebidos[1]


@pytest.mark.asyncio
async def test_diario_entra_no_contexto(bus):
    recebidos = []

    async def compor(nome, descricao, contexto, evitar):
        recebidos.append(contexto)
        return "oi"

    async def observacoes():
        return [{"quando": "14:30", "o_que": "vi a Marah"}]

    _montar(bus, compor=compor, observacoes=observacoes, relogio_parede=_hora(15))
    await _ver(bus, "Kamal")

    assert "14:30" in recebidos[0]
    assert "vi a Marah" in recebidos[0]


@pytest.mark.asyncio
async def test_diario_quebrado_nao_impede_o_cumprimento(bus):
    """O diario e enfeite no cumprimento, nao requisito (Cap 6 s.8). Ele le
    pela rede, ate o Pi - e a rede cai."""
    async def compor(nome, descricao, contexto, evitar):
        return f"oi, {nome}"

    async def observacoes():
        raise RuntimeError("Pi fora do ar")

    _, ditas = _montar(bus, compor=compor, observacoes=observacoes)
    await _ver(bus, "Kamal")

    assert ditas == ["oi, Kamal"]


# ----- anti-repeticao (2026-07-29) -----
#
# Medido: com a hora fora do prompt (pedido do dono) o modelo convergiu -
# 5 chamadas, 4 vezes "Que bom te ver". As duas exigencias do dono brigam:
# sem poder recitar fato nenhum, so resta variar palavras numa tarefa de seis
# palavras. Dizer o que JA FOI DITO e o unico jeito de forcar palavra nova sem
# reintroduzir o relatorio de dados.


@pytest.mark.asyncio
async def test_frases_ja_ditas_chegam_ao_compositor(bus):
    recebidas_para_evitar = []
    relogio = _Relogio()

    async def compor(nome, descricao, contexto, evitar):
        recebidas_para_evitar.append(list(evitar))
        return f"cumprimento numero {len(recebidas_para_evitar)}"

    _montar(bus, compor=compor, relogio=relogio)

    await _ver(bus, "Kamal")
    relogio.avancar(3600)
    await _ver(bus, "Kamal")
    relogio.avancar(3600)
    await _ver(bus, "Kamal")

    assert recebidas_para_evitar[0] == [], "na primeira vez nao ha nada a evitar"
    assert recebidas_para_evitar[1] == ["cumprimento numero 1"]
    assert recebidas_para_evitar[2] == ["cumprimento numero 1", "cumprimento numero 2"]


@pytest.mark.asyncio
async def test_lembra_no_maximo_tres_frases(bus):
    """Sem teto o prompt cresceria para sempre - e pedir para nunca repetir
    nada seria pedir o impossivel."""
    recebidas = []
    relogio = _Relogio()

    async def compor(nome, descricao, contexto, evitar):
        recebidas.append(list(evitar))
        return f"frase {len(recebidas)}"

    _montar(bus, compor=compor, relogio=relogio)

    for _ in range(6):
        await _ver(bus, "Marah")
        relogio.avancar(3600)

    assert len(recebidas[-1]) == SaudacaoEspontanea.MAX_FRASES_LEMBRADAS
    # as tres ULTIMAS, nao as tres primeiras
    assert recebidas[-1] == ["frase 3", "frase 4", "frase 5"]


@pytest.mark.asyncio
async def test_lembra_tambem_a_frase_da_lista_de_reserva(bus):
    """O que importa e o que a pessoa OUVIU, nao de onde a frase veio. Se a IA
    cair e a reserva falar, a proxima chamada tem que saber disso."""
    recebidas = []
    relogio = _Relogio()
    chamadas = {"n": 0}

    async def compor(nome, descricao, contexto, evitar):
        recebidas.append(list(evitar))
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise RuntimeError("internet caiu")  # cai na lista de reserva
        return "frase da IA"

    _montar(bus, compor=compor, relogio=relogio)

    await _ver(bus, "João Paulo")
    relogio.avancar(3600)
    await _ver(bus, "João Paulo")

    # `escolher_frase` no _montar devolve sempre a primeira da relacao
    assert recebidas[1] == [FRASES_POR_RELACAO["criador"][0].format(nome="João Paulo")]


@pytest.mark.asyncio
async def test_cada_pessoa_tem_a_sua_propria_memoria_de_frases(bus):
    recebidas = {}
    relogio = _Relogio()

    async def compor(nome, descricao, contexto, evitar):
        recebidas.setdefault(nome, []).append(list(evitar))
        return f"oi {nome}"

    _montar(bus, compor=compor, relogio=relogio)

    await _ver(bus, "Kamal")
    await _ver(bus, "Marah")
    relogio.avancar(3600)
    await _ver(bus, "Kamal")

    assert recebidas["Kamal"][1] == ["oi Kamal"], "nao mistura com a outra pessoa"
    assert recebidas["Marah"][0] == []


# ---------------------------------------------------------------------------
# RECADOS (2026-08-03)
#
# O dono pediu "avise a Mara quando a vir que eu quero falar com ela". O robô
# respondeu que sim, a Mara chegou, ele a CUMPRIMENTOU - e não deu o recado.
# Nada falhou: não havia nada. Estes testes são a rede que faltava.
# ---------------------------------------------------------------------------


class CaixaDeRecadosFalsa:
    def __init__(self, pendentes=None, explodir: str = "") -> None:
        self._pendentes = list(pendentes or [])
        self.entregues: list[int] = []
        self._explodir = explodir

    async def pendentes(self, nome: str):
        if self._explodir == "pendentes":
            raise RuntimeError("banco fora do ar (simulado)")
        return [r for r in self._pendentes if r["para_nome"] == nome]

    async def marcar_entregue(self, recado_id: int) -> bool:
        self.entregues.append(recado_id)
        self._pendentes = [r for r in self._pendentes if r["id"] != recado_id]
        return True


async def _ceder(vezes: int = 20) -> None:
    """O cumprimento roda fora do laço da visão e a entrega vem depois dele -
    são mais awaits do que o `_ver` cede sozinho."""
    for _ in range(vezes):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_entrega_o_recado_depois_de_cumprimentar(bus):
    """A ORDEM É O TESTE. Chegar em casa e ouvir um recado antes de um "oi" é
    o robô tratando a pessoa como caixa de entrada."""
    caixa = CaixaDeRecadosFalsa(
        [{"id": 7, "para_nome": "Marah", "texto": "o João Paulo quer falar com você"}]
    )
    _, ditas = _montar(bus, recados=caixa)

    await _ver(bus, "Marah")
    await _ceder()

    assert len(ditas) == 2
    assert "Marah" in ditas[0], "o cumprimento vem primeiro"
    assert ditas[1] == "o João Paulo quer falar com você"


@pytest.mark.asyncio
async def test_recado_entregue_e_marcado(bus):
    """Sem isto ele repetiria o mesmo recado a cada chegada, para sempre."""
    caixa = CaixaDeRecadosFalsa([{"id": 7, "para_nome": "Marah", "texto": "oi"}])
    _montar(bus, recados=caixa)

    await _ver(bus, "Marah")
    await _ceder()

    assert caixa.entregues == [7]


@pytest.mark.asyncio
async def test_recado_de_outra_pessoa_nao_e_lido_em_voz_alta(bus):
    """Recado é entre duas pessoas. Ler o da Marah para o Kamal é pior que
    não entregar."""
    caixa = CaixaDeRecadosFalsa(
        [{"id": 7, "para_nome": "Marah", "texto": "segredo da Marah"}]
    )
    _, ditas = _montar(bus, recados=caixa)

    await _ver(bus, "Kamal")
    await _ceder()

    assert len(ditas) == 1
    assert "segredo" not in ditas[0]


@pytest.mark.asyncio
async def test_sem_caixa_de_recados_ele_ainda_cumprimenta(bus):
    """Degradação graciosa (Cap 6 s.8): sem banco, cumprimenta e não entrega -
    em vez de não cumprimentar."""
    _, ditas = _montar(bus)

    await _ver(bus, "Marah")
    await _ceder()

    assert len(ditas) == 1


@pytest.mark.asyncio
async def test_falha_ao_consultar_recados_nao_derruba_o_cumprimento(bus):
    caixa = CaixaDeRecadosFalsa(explodir="pendentes")
    _, ditas = _montar(bus, recados=caixa)

    await _ver(bus, "Marah")
    await _ceder()

    assert len(ditas) == 1, "o cumprimento tem que ter saído assim mesmo"
