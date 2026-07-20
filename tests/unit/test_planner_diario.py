"""Perguntas sobre quem o robô viu são respondidas por CONSULTA, não pela IA.

Medido ao vivo em 2026-07-19, com o diário contendo apenas "09:12 vi Bruno"
e perguntando sobre a Ana:

    gemma3:1b -> "Sim, vi. às 09:12."
    gemma3:4b -> "Sim, eu vi a Ana às 09:12!"

Os dois viram um registro com hora e responderam que sim, sem conferir DE
QUEM era. E não é questão de redação: com ênfase em não inventar, o 1b
passou a negar até o que ESTAVA no diário; com ênfase em usar os fatos,
passou a afirmar o que não estava. Ele segue o tom da instrução, não os
dados.

Isso é consulta a banco. Fica no código; a IA fica com a conversa livre.
"""
from __future__ import annotations

import pytest

from orion.mission.mission_planner import MissionPlanner, _mesmo_nome


class _IaFalsa:
    def __init__(self) -> None:
        self.chamada = False

    async def responder(self, texto: str, contexto=None) -> str:
        self.chamada = True
        return "resposta da IA"


class _DiarioFalso:
    async def observacoes_de_hoje(self):
        return []


def _planner(ia: _IaFalsa) -> MissionPlanner:
    return MissionPlanner(ia, diario=_DiarioFalso())


CONTEXTO_COM_BRUNO = {"observacoes": [{"quando": "09:12", "o_que": "vi Bruno"}]}
CONTEXTO_COM_ANA = {"observacoes": [{"quando": "14:30", "o_que": "vi Ana"}]}
CONTEXTO_VAZIO: dict = {"observacoes": []}


# ----- extração do nome -----


@pytest.mark.parametrize(
    "pergunta,esperado",
    [
        ("voce viu a Ana hoje?", "Ana"),
        ("viu o Bruno?", "Bruno"),
        ("você viu Ana por aqui?", "Ana"),
        ("viu o João Paulo hoje?", "João Paulo"),
    ],
)
def test_extrai_o_nome_perguntado(pergunta: str, esperado: str) -> None:
    assert MissionPlanner._extrair_nome_perguntado(pergunta) == esperado


@pytest.mark.parametrize(
    "pergunta",
    ["que horas sao?", "acende a lanterna", "viu alguem?", "como voce esta?"],
)
def test_pergunta_que_nao_e_sobre_pessoa_devolve_none(pergunta: str) -> None:
    assert MissionPlanner._extrair_nome_perguntado(pergunta) is None


# ----- casamento de nome -----


def test_primeiro_nome_casa_com_nome_completo() -> None:
    """Quem pergunta diz "o João"; o diário guarda "João Paulo"."""
    assert _mesmo_nome("João", "vi João Paulo") is True


def test_nome_de_outra_pessoa_nao_casa() -> None:
    """O caso exato que os dois modelos erraram."""
    assert _mesmo_nome("Ana", "vi Bruno") is False


def test_acento_e_caixa_nao_atrapalham() -> None:
    assert _mesmo_nome("joao", "vi João Paulo") is True


# ----- resposta -----


@pytest.mark.asyncio
async def test_com_registro_responde_sim_com_a_hora() -> None:
    ia = _IaFalsa()
    resposta = await _planner(ia)._responder_sobre_quem_viu(
        "voce viu a Ana hoje?", CONTEXTO_COM_ANA
    )
    assert resposta is not None
    assert "14:30" in resposta
    assert ia.chamada is False, "não devia ter passado pela IA"


@pytest.mark.asyncio
async def test_registro_de_outra_pessoa_responde_NAO() -> None:
    """O teste que importa: diário tem Bruno, perguntaram da Ana."""
    resposta = await _planner(_IaFalsa())._responder_sobre_quem_viu(
        "voce viu a Ana hoje?", CONTEXTO_COM_BRUNO
    )
    assert resposta is not None
    assert resposta.lower().startswith("nao")
    assert "09:12" not in resposta, "atribuiu o horário do Bruno à Ana"


@pytest.mark.asyncio
async def test_diario_vazio_responde_que_nao_viu() -> None:
    resposta = await _planner(_IaFalsa())._responder_sobre_quem_viu(
        "viu o Bruno?", CONTEXTO_VAZIO
    )
    assert resposta is not None
    assert resposta.lower().startswith("nao")


@pytest.mark.asyncio
async def test_varios_registros_da_mesma_pessoa() -> None:
    contexto = {
        "observacoes": [
            {"quando": "09:12", "o_que": "vi Ana"},
            {"quando": "18:40", "o_que": "vi Ana"},
        ]
    }
    resposta = await _planner(_IaFalsa())._responder_sobre_quem_viu(
        "viu a Ana hoje?", contexto
    )
    assert "09:12" in resposta and "18:40" in resposta


@pytest.mark.asyncio
async def test_pergunta_normal_continua_indo_para_a_ia() -> None:
    """A consulta determinística não pode sequestrar a conversa livre."""
    resposta = await _planner(_IaFalsa())._responder_sobre_quem_viu(
        "como foi o seu dia?", CONTEXTO_COM_ANA
    )
    assert resposta is None


@pytest.mark.asyncio
async def test_sem_diario_nao_intercepta() -> None:
    planner = MissionPlanner(_IaFalsa())  # sem diário
    assert await planner._responder_sobre_quem_viu(
        "viu a Ana?", CONTEXTO_COM_ANA
    ) is None


@pytest.mark.asyncio
async def test_fluxo_completo_nao_chama_a_ia_para_essa_pergunta() -> None:
    ia = _IaFalsa()

    class _DiarioComAna:
        async def observacoes_de_hoje(self):
            return [{"quando": "14:30", "o_que": "vi Ana"}]

    planner = MissionPlanner(ia, diario=_DiarioComAna())
    resposta = await planner.processar("voce viu a Ana hoje?")

    assert "14:30" in resposta
    assert ia.chamada is False
