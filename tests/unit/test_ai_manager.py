"""Testes do AI Manager (Cap 7 s.2-3) - montagem do prompt de sistema e a
escolha/fallback de backend (EDR-0021), sem chamar Ollama/Gemini de
verdade (isso fica para um teste manual/real)."""
import pytest

from orion.mission.ai_manager import AiManager


@pytest.fixture
def ai_manager(tmp_path):
    caminho_prompt = tmp_path / "prompt.txt"
    caminho_prompt.write_text("Voce e o Fofão.", encoding="utf-8")
    return AiManager(caminho_prompt_sistema=caminho_prompt)


def test_sem_contexto_o_prompt_diz_o_que_nao_se_sabe(ai_manager):
    """Ate 2026-07-25 este teste exigia que o prompt fosse SO a frase base.
    O grounding (merge de 2026-07-26) mudou isso de proposito: mesmo sem
    dado nenhum, o bloco de fatos entra dizendo "nao sei (sem registro)".
    Campo ausente e convite a invencao - o modelo respondia "Sim, vi a Ana
    passar!" sobre quem nunca viu (medido 2026-07-19, ver grounding.py)."""
    prompt = ai_manager._montar_prompt_sistema(None)
    assert "Voce e o Fofão." in prompt
    assert "não sei (sem registro)" in prompt


def test_contexto_vazio_o_prompt_diz_o_que_nao_se_sabe(ai_manager):
    prompt = ai_manager._montar_prompt_sistema({})
    assert "Voce e o Fofão." in prompt
    assert "não sei (sem registro)" in prompt


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


def test_contexto_com_notas_do_vault_adiciona_ao_prompt(ai_manager):
    contexto = {"notas_relevantes": [{"titulo": "Aniversario da Marah", "trecho": "12 de marco"}]}
    prompt = ai_manager._montar_prompt_sistema(contexto)
    assert "Aniversario da Marah" in prompt
    assert "12 de marco" in prompt


@pytest.mark.asyncio
async def test_provider_ollama_nunca_chama_openai(ai_manager, monkeypatch):
    ai_manager._provider = "ollama"
    monkeypatch.setattr(ai_manager, "_chamar_ollama", lambda *_: "resposta ollama")
    monkeypatch.setattr(
        ai_manager, "_chamar_openai", lambda *_: (_ for _ in ()).throw(AssertionError("nao deveria chamar"))
    )

    resposta = await ai_manager.responder("oi")

    assert resposta == "resposta ollama"


@pytest.mark.asyncio
async def test_provider_openai_sem_chave_usa_ollama_direto(ai_manager, monkeypatch):
    ai_manager._provider = "openai"
    ai_manager._openai_api_key = None
    monkeypatch.setattr(ai_manager, "_chamar_ollama", lambda *_: "resposta ollama")

    resposta = await ai_manager.responder("oi")

    assert resposta == "resposta ollama"


@pytest.mark.asyncio
async def test_provider_openai_com_chave_usa_openai(ai_manager, monkeypatch):
    ai_manager._provider = "openai"
    ai_manager._openai_api_key = "chave-de-teste"
    monkeypatch.setattr(ai_manager, "_chamar_openai", lambda *_: "resposta openai")

    resposta = await ai_manager.responder("oi")

    assert resposta == "resposta openai"


@pytest.mark.asyncio
async def test_decidir_retorna_acao_do_vocabulario(ai_manager, monkeypatch):
    async def _responder_falso(prompt, contexto=None):
        return "observar_ambiente"

    monkeypatch.setattr(ai_manager, "responder", _responder_falso)

    acao = await ai_manager.decidir({}, ["descansar", "observar_ambiente"])

    assert acao == "observar_ambiente"


@pytest.mark.asyncio
async def test_decidir_aceita_resposta_com_texto_extra_ao_redor(ai_manager, monkeypatch):
    """A IA nem sempre obedece "so a palavra exata" - aceitar por
    substring e mais robusto do que exigir igualdade estrita."""
    async def _responder_falso(prompt, contexto=None):
        return "Acho que a melhor opcao e Observar_Ambiente agora."

    monkeypatch.setattr(ai_manager, "responder", _responder_falso)

    acao = await ai_manager.decidir({}, ["descansar", "observar_ambiente"])

    assert acao == "observar_ambiente"


@pytest.mark.asyncio
async def test_decidir_fora_do_vocabulario_retorna_none(ai_manager, monkeypatch):
    async def _responder_falso(prompt, contexto=None):
        return "vou dancar"

    monkeypatch.setattr(ai_manager, "responder", _responder_falso)

    acao = await ai_manager.decidir({}, ["descansar", "observar_ambiente"])

    assert acao is None


@pytest.mark.asyncio
async def test_decidir_com_falha_retorna_none(ai_manager, monkeypatch):
    async def _responder_com_falha(prompt, contexto=None):
        raise RuntimeError("sem internet (simulado)")

    monkeypatch.setattr(ai_manager, "responder", _responder_com_falha)

    acao = await ai_manager.decidir({}, ["descansar", "observar_ambiente"])

    assert acao is None


@pytest.mark.asyncio
async def test_openai_falha_cai_para_ollama(ai_manager, monkeypatch):
    ai_manager._provider = "openai"
    ai_manager._openai_api_key = "chave-de-teste"

    def _openai_com_falha(*_):
        raise RuntimeError("sem internet (simulado)")

    monkeypatch.setattr(ai_manager, "_chamar_openai", _openai_com_falha)
    monkeypatch.setattr(ai_manager, "_chamar_ollama", lambda *_: "resposta ollama de reserva")

    resposta = await ai_manager.responder("oi")

    assert resposta == "resposta ollama de reserva"
