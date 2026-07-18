"""Testes do AI Manager (Cap 7 s.2-3) - so a montagem do prompt de sistema
(sem chamar o Ollama de verdade, que fica para um teste manual/real)."""
import pytest

pytest.importorskip("ollama")

from orion.mission.ai_manager import AiManager  # noqa: E402


@pytest.fixture
def ai_manager(tmp_path):
    caminho_prompt = tmp_path / "prompt.txt"
    caminho_prompt.write_text("Voce e o Fofão.", encoding="utf-8")
    return AiManager(caminho_prompt_sistema=caminho_prompt)


def test_sem_contexto_usa_prompt_base(ai_manager):
    assert ai_manager._montar_prompt_sistema(None) == "Voce e o Fofão."


def test_contexto_vazio_usa_prompt_base(ai_manager):
    assert ai_manager._montar_prompt_sistema({}) == "Voce e o Fofão."


def test_contexto_com_pessoa_adiciona_ao_prompt(ai_manager):
    prompt = ai_manager._montar_prompt_sistema({"pessoa": {"nome": "Joao"}})
    assert "Voce e o Fofão." in prompt
    assert "Joao" in prompt


def test_contexto_com_conversas_adiciona_historico(ai_manager):
    contexto = {
        "conversas_recentes": [
            {"papel": "usuario", "texto": "oi"},
            {"papel": "robo", "texto": "ola"},
        ]
    }
    prompt = ai_manager._montar_prompt_sistema(contexto)
    assert "usuario: oi" in prompt
    assert "robo: ola" in prompt


def test_contexto_com_conhecimento_adiciona_fatos(ai_manager):
    contexto = {"conhecimento_relevante": [{"chave": "cor_favorita", "valor": "azul"}]}
    prompt = ai_manager._montar_prompt_sistema(contexto)
    assert "cor_favorita: azul" in prompt
