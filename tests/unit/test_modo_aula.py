"""Testes do modo aula (2026-08-09).

Pedido do dono: "ativar modo aula - ele entra com o sistema de cursos e
aulas disponiveis e pergunta qual aula ou curso voce gostaria de comecar".

O que protegem: o cardapio sai com a pergunta, a resposta seguinte vale
como escolha MESMO SEM a palavra "aula", a escuta expira em um turno (modo
grudento e sequestro), e fora do modo o nome seco nao inicia aula nenhuma.
"""
from types import SimpleNamespace

import pytest

from orion.mission.mission_planner import MissionPlanner


class AiManagerFalso:
    def __init__(self, resposta: str = "resposta da IA") -> None:
        self._resposta = resposta
        self.chamadas = []

    async def responder(self, texto_usuario, contexto=None, **kwargs):
        self.chamadas.append(texto_usuario)
        return self._resposta


class ProfessorFalso:
    dando_aula = False

    def __init__(self) -> None:
        self.dadas = []
        self.cardapio_pedido = 0

    def apresentar_cardapio(self) -> str:
        self.cardapio_pedido += 1
        return "Modo aula ativado. Qual aula ou curso voce gostaria de comecar?"

    def procurar(self, texto: str, nivel: str | None = None):
        if "banco de dados" in texto.lower():
            return SimpleNamespace(nivel="avancado", titulo="Banco de dados")
        return None

    async def dar_aula(self, aula) -> None:
        self.dadas.append(aula)

    async def parar(self) -> None:
        pass

    def oferecer_o_que_tem(self) -> str:
        return "Dessa eu ainda nao tenho aula. Por enquanto eu sei dar de programacao."


@pytest.mark.asyncio
async def test_modo_aula_apresenta_cardapio_com_a_pergunta():
    ai = AiManagerFalso()
    professor = ProfessorFalso()
    planner = MissionPlanner(ai_manager=ai, professor=professor)

    resposta = await planner.processar("ativar modo aula")

    assert "Qual aula ou curso" in resposta
    assert professor.cardapio_pedido == 1
    assert professor.dadas == []  # cardapio nao e escolha: nenhuma aula comeca
    assert ai.chamadas == []  # sem IA: o cardapio vem do banco


@pytest.mark.asyncio
async def test_escolha_sem_a_palavra_aula_inicia_a_aula():
    professor = ProfessorFalso()
    planner = MissionPlanner(ai_manager=AiManagerFalso(), professor=professor)

    await planner.processar("ativar modo aula")
    resposta = await planner.processar("banco de dados")

    assert len(professor.dadas) == 1
    assert "Banco de dados" in resposta
    assert "avancada" in resposta  # a abertura diz o nivel escolhido


@pytest.mark.asyncio
async def test_fora_do_modo_o_nome_seco_nao_inicia_aula():
    """Sem o cardapio antes, "banco de dados" e conversa, nao escolha."""
    ai = AiManagerFalso()
    professor = ProfessorFalso()
    planner = MissionPlanner(ai_manager=ai, professor=professor)

    resposta = await planner.processar("banco de dados")

    assert resposta == "resposta da IA"
    assert professor.dadas == []


@pytest.mark.asyncio
async def test_escolha_desconhecida_oferece_o_que_tem_e_a_escuta_expira():
    ai = AiManagerFalso()
    professor = ProfessorFalso()
    planner = MissionPlanner(ai_manager=ai, professor=professor)

    await planner.processar("ativar modo aula")
    resposta = await planner.processar("aula de fisica quantica")
    assert "nao tenho aula" in resposta

    # a escuta era de UM turno: o nome seco seguinte ja e conversa normal
    resposta = await planner.processar("banco de dados")
    assert resposta == "resposta da IA"
    assert professor.dadas == []


@pytest.mark.asyncio
async def test_desistir_na_janela_cancela_sem_aula():
    professor = ProfessorFalso()
    planner = MissionPlanner(ai_manager=AiManagerFalso(), professor=professor)

    await planner.processar("ativar modo aula")
    resposta = await planner.processar("deixa pra la")

    assert "sem aula" in resposta
    assert professor.dadas == []


@pytest.mark.asyncio
async def test_pedir_o_cardapio_de_vem_na_janela_reapresenta():
    professor = ProfessorFalso()
    planner = MissionPlanner(ai_manager=AiManagerFalso(), professor=professor)

    await planner.processar("ativar modo aula")
    resposta = await planner.processar("modo aula")

    assert "Qual aula ou curso" in resposta
    assert professor.cardapio_pedido == 2
    assert professor.dadas == []
