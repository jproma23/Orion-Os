"""Os bugs do chat de 14 e 15/11, cada um com a frase real do log.

- "atom tb tem" leu em voz alta a ficha "Relacao dono e Atom" (saber sem pergunta)
- "Eu vi voce digitar isso as 06:55" (a fala de agora voltava como fato do diario)
- "me siga" com a camera cega girou 10 s procurando ninguem
- "re" virou "Avancando mais um pouco"
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from orion.kernel.event_bus import EventBus
from orion.mission.acoes_faladas import _CATALOGO


def acao_por_nome(nome):
    return next(a for a in _CATALOGO if a.nome == nome)
from orion.mission.diario import ORIGEM, TIPO_PEDIDO_OUVIDO, DiarioObservacoes
from orion.mission.grounding import bloco_observacoes
from orion.mission.mission_planner import MissionPlanner, _eh_pergunta

from test_mission_planner import AiManagerFalso, MemoryClientFalso

AGORA = datetime(2026, 11, 15, 6, 55, 40)


# ------------------------------------------------------------ saber so pergunta
@pytest.mark.parametrize(
    "frase",
    ["atom tb tem", "é original", "eu sou bem, eu quero que você me siga", "Júlia do... Júlia do..."],
)
def test_comentario_nao_e_pergunta(frase):
    assert not _eh_pergunta(frase), frase


@pytest.mark.parametrize(
    "frase",
    [
        "qual e o seu nome e qual sistema roda em voce",
        "o que e um BME280?",
        "como mede a distancia",
        "Atom, quem é o João Paulo",
        "para que serve um BME280?",
        "me fala sobre o LiDAR",
    ],
)
def test_pergunta_continua_pergunta(frase):
    assert _eh_pergunta(frase), frase


@pytest.mark.asyncio
async def test_comentario_com_palavra_da_ficha_vai_para_ia_e_nao_le_a_ficha():
    ai = AiManagerFalso(resposta="Tem sim, e original.")
    memoria = MemoryClientFalso()
    memoria.saber_plantar("Relacao dono e Atom", "Joao Paulo e o engenheiro...")

    planner = MissionPlanner(ai_manager=ai, memory_client=memoria)
    resposta = await planner.processar("atom tb tem")

    assert "Relacao" not in resposta
    assert ai.chamadas
    assert memoria.buscas_do_saber == []  # nem consultou o acervo


# ------------------------------------------------------ a fala de agora no diario
def _pedido(texto: str, quando: datetime) -> dict:
    return {
        "origem": ORIGEM,
        "tipo": TIPO_PEDIDO_OUVIDO,
        "payload_json": json.dumps({"texto": texto}, ensure_ascii=False),
        "timestamp": quando.isoformat(),
    }


class _Memoria:
    def __init__(self, registros):
        self._registros = registros

    async def recall(self, categoria, filtro=None, limite=20):
        return self._registros[:limite]


@pytest.mark.asyncio
async def test_a_fala_de_agora_nao_volta_como_fato_do_diario():
    fala = "Girds de Tras Essas Stagraos"
    registros = [  # recall: mais recente primeiro
        _pedido(fala, AGORA),
        _pedido("oi", AGORA - timedelta(minutes=1)),
    ]
    diario = DiarioObservacoes(EventBus(), _Memoria(registros), agora=lambda: AGORA)

    obs = await diario.observacoes_de_hoje(sem_o_pedido=fala)

    assert [o["o_que"] for o in obs] == ['me pediram: "oi"']


@pytest.mark.asyncio
async def test_pedido_repetido_de_antes_continua_no_diario():
    """So a gravacao mais recente e a de agora: pedir 3x a mesma coisa ainda
    aparece - e o diario existe para isso."""
    registros = [_pedido("me siga", AGORA), _pedido("me siga", AGORA - timedelta(minutes=5))]
    diario = DiarioObservacoes(EventBus(), _Memoria(registros), agora=lambda: AGORA)

    obs = await diario.observacoes_de_hoje(sem_o_pedido="me siga")

    assert len(obs) == 1


def test_pedido_nao_vira_eu_vi():
    bloco = bloco_observacoes([{"quando": "06:54", "o_que": 'me pediram: "oi"'}])
    assert "eu vi: me pediram" not in bloco
    assert '06:54: me pediram: "oi"' in bloco


# ------------------------------------------------------------- seguir sem ver
@pytest.mark.asyncio
async def test_me_siga_com_camera_cega_recusa_e_diz_o_motivo():
    pedidos = []

    async def comandar_navegacao(dados):
        pedidos.append(dados)

    planner = MissionPlanner(ai_manager=AiManagerFalso(), comandar_navegacao=comandar_navegacao)
    planner.registrar_consultar_visao(
        lambda: None, motivo_cego=lambda: "a camera nao manda imagem nova ha alguns segundos"
    )
    falado = await planner._cumprir_acao_pedida(
        "#acao:me_siga Pronto, ja estou atras de voce.", "eu quero que voce me siga"
    )

    assert pedidos == []
    assert "camera" in falado
    assert "atras de voce" not in falado


@pytest.mark.asyncio
async def test_me_siga_enxergando_segue_normal():
    pedidos = []

    async def comandar_navegacao(dados):
        pedidos.append(dados)

    planner = MissionPlanner(ai_manager=AiManagerFalso(), comandar_navegacao=comandar_navegacao)
    planner.registrar_consultar_visao(lambda: {"objetos": []}, motivo_cego=lambda: None)
    await planner._cumprir_acao_pedida("#acao:me_siga", "me siga")

    assert pedidos == [{"acao": "FOLLOW"}]


def test_mapear_nao_depende_da_camera():
    assert not acao_por_nome("mapear_casa").precisa_ver


# ----------------------------------------------------------------------- re
def test_re_sozinho_esta_na_descricao_da_re():
    assert '"re"' in acao_por_nome("andar_para_tras").descricao
