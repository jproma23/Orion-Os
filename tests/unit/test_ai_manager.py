"""Testes do AI Manager (Cap 7 s.2-3) - montagem do prompt de sistema e o que
acontece quando a IA remota nao responde, sem chamar a API de verdade."""
import pytest

from orion.mission.ai_manager import SEM_IA, AiManager


@pytest.fixture
def ai_manager(tmp_path):
    caminho_prompt = tmp_path / "prompt.txt"
    caminho_prompt.write_text("Voce e o Javis.", encoding="utf-8")
    return AiManager(caminho_prompt_sistema=caminho_prompt)


def test_sem_contexto_o_prompt_diz_o_que_nao_se_sabe(ai_manager):
    """Ate 2026-07-25 este teste exigia que o prompt fosse SO a frase base.
    O grounding (merge de 2026-07-26) mudou isso de proposito: mesmo sem
    dado nenhum, o bloco de fatos entra dizendo "nao sei (sem registro)".
    Campo ausente e convite a invencao - o modelo respondia "Sim, vi a Ana
    passar!" sobre quem nunca viu (medido 2026-07-19, ver grounding.py)."""
    prompt = ai_manager._montar_prompt_sistema(None)
    assert "Voce e o Javis." in prompt
    assert "não sei (sem registro)" in prompt


def test_contexto_vazio_o_prompt_diz_o_que_nao_se_sabe(ai_manager):
    prompt = ai_manager._montar_prompt_sistema({})
    assert "Voce e o Javis." in prompt
    assert "não sei (sem registro)" in prompt


def test_contexto_com_pessoa_adiciona_ao_prompt(ai_manager):
    prompt = ai_manager._montar_prompt_sistema({"pessoa": {"nome": "Joao"}})
    assert "Voce e o Javis." in prompt
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
async def test_sem_chave_ele_avisa_em_vez_de_ficar_pendurado(ai_manager):
    """Desde 2026-08-05 nao ha reserva local. Sem chave a resposta e o aviso -
    e nunca uma espera: era isso que o Ollama fazia quando foi preciso."""
    ai_manager._openai_api_key = None

    assert await ai_manager.responder("oi") == SEM_IA


@pytest.mark.asyncio
async def test_aviso_de_sem_ia_nao_entra_no_historico(ai_manager):
    """Guardado, ele viraria "assunto anterior" e a resposta seguinte sairia
    comentando a propria falha."""
    ai_manager._openai_api_key = None

    await ai_manager.responder("oi")

    assert len(ai_manager.historico) == 0


@pytest.mark.asyncio
async def test_provider_openai_com_chave_usa_openai(ai_manager, monkeypatch):
    ai_manager._openai_api_key = "chave-de-teste"
    monkeypatch.setattr(ai_manager, "_chamar_openai", lambda *_: "resposta openai")

    resposta = await ai_manager.responder("oi")

    assert resposta == "resposta openai"


@pytest.mark.asyncio
async def test_decidir_retorna_acao_do_vocabulario(ai_manager, monkeypatch):
    async def _responder_falso(prompt, contexto=None, usar_historico=True):
        return "observar_ambiente"

    monkeypatch.setattr(ai_manager, "responder", _responder_falso)

    acao = await ai_manager.decidir({}, ["descansar", "observar_ambiente"])

    assert acao == "observar_ambiente"


@pytest.mark.asyncio
async def test_decidir_aceita_resposta_com_texto_extra_ao_redor(ai_manager, monkeypatch):
    """A IA nem sempre obedece "so a palavra exata" - aceitar por
    substring e mais robusto do que exigir igualdade estrita."""
    async def _responder_falso(prompt, contexto=None, usar_historico=True):
        return "Acho que a melhor opcao e Observar_Ambiente agora."

    monkeypatch.setattr(ai_manager, "responder", _responder_falso)

    acao = await ai_manager.decidir({}, ["descansar", "observar_ambiente"])

    assert acao == "observar_ambiente"


@pytest.mark.asyncio
async def test_decidir_fora_do_vocabulario_retorna_none(ai_manager, monkeypatch):
    async def _responder_falso(prompt, contexto=None, usar_historico=True):
        return "vou dancar"

    monkeypatch.setattr(ai_manager, "responder", _responder_falso)

    acao = await ai_manager.decidir({}, ["descansar", "observar_ambiente"])

    assert acao is None


@pytest.mark.asyncio
async def test_decidir_com_falha_retorna_none(ai_manager, monkeypatch):
    async def _responder_com_falha(prompt, contexto=None, usar_historico=True):
        raise RuntimeError("sem internet (simulado)")

    monkeypatch.setattr(ai_manager, "responder", _responder_com_falha)

    acao = await ai_manager.decidir({}, ["descansar", "observar_ambiente"])

    assert acao is None


@pytest.mark.asyncio
async def test_ia_remota_que_falha_devolve_o_aviso_e_nao_levanta(ai_manager, monkeypatch):
    """O caso REAL de 2026-08-05: os creditos do OpenRouter acabaram e a API
    passou a devolver 402. O robo tem que dizer que ficou sem pensar - nao
    explodir, e nao pendurar quem esta falando com ele."""
    ai_manager._openai_api_key = "chave-de-teste"

    def _openai_com_falha(*_):
        raise RuntimeError("Error code: 402 - Insufficient credits (simulado)")

    monkeypatch.setattr(ai_manager, "_chamar_openai", _openai_com_falha)

    assert await ai_manager.responder("oi") == SEM_IA


# ---------------------------------------------------------------------------
# `descarregar()` SAIU COM O OLLAMA (2026-08-05)
#
# Os dois testes que viviam aqui cobriam o bug de 30/07 (o metodo usava
# `self._cliente`, atributo que nunca existiu, e o AlivioCarga engolia o
# AttributeError - o guardiao de RAM aparecia inteiro no log sem liberar um
# byte). Sem modelo local nao ha o que descarregar, e o metodo foi removido em
# vez de esvaziado, justamente para nao repetir aquilo com outra fachada.
#
# A regressao que sobra vale para o ALIVIO, nao para esta classe, e mora em
# tests/unit/test_alivio_carga.py: cada acao e isolada e `descarregar_modelo`
# ausente nao pode impedir a visao de ser pausada.
# ---------------------------------------------------------------------------


def test_descarregar_nao_existe_mais(ai_manager):
    """Contraprova: se alguem reintroduzir o metodo sem reintroduzir um modelo
    local, o alivio volta a "atender" sem liberar nada."""
    assert not hasattr(ai_manager, "descarregar")
    assert not hasattr(ai_manager, "_cliente_ollama")

