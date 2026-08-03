"""A conversa falada lembra do que foi dito - e esquece o que nao e conversa.

Queixa que originou isto (dono, 2026-08-02): *"a conversa ja muda de assunto,
nao tem memoria"*. Nao era memoria curta, era memoria nenhuma: os tres
caminhos que falam com o modelo montavam `messages` com system + user e mais
nada.
"""
from __future__ import annotations

import pytest

from orion.mission.ai_manager import AiManager
from orion.mission.historico_conversa import HistoricoConversa


class RelogioFalso:
    def __init__(self) -> None:
        self.agora = 1000.0

    def __call__(self) -> float:
        return self.agora

    def avancar(self, segundos: float) -> None:
        self.agora += segundos


@pytest.fixture
def ia(monkeypatch):
    """AiManager com o backend trocado por um espiao.

    `capturado` guarda o `messages` da ULTIMA chamada - e o que os testes
    precisam ver, porque o bug morava exatamente ali.
    """
    gerente = AiManager(provider="ollama")
    capturado: dict = {}

    def _falso(mensagens):
        capturado["mensagens"] = mensagens
        return f"resposta {len(mensagens)}"

    monkeypatch.setattr(gerente, "_chamar_ollama", _falso)
    gerente.capturado = capturado
    return gerente


# --------------------------------------------------------------------------
# O bug relatado
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segunda_pergunta_carrega_a_primeira():
    """O teste que descreve a queixa.

    Antes desta mudanca a segunda pergunta chegava ao modelo com DUAS
    mensagens - system + user - exatamente como a primeira. O robo nao mudava
    de assunto: ele nunca soube que havia um assunto.
    """
    gerente = AiManager(provider="ollama")
    vistas: list[list[dict]] = []

    def _falso(mensagens):
        vistas.append(list(mensagens))
        return "entendi"

    gerente._chamar_ollama = _falso

    await gerente.responder("meu cachorro chama Rex")
    await gerente.responder("qual a idade dele?")

    # Primeira: system + user.
    assert [m["role"] for m in vistas[0]] == ["system", "user"]
    # Segunda: system + o turno anterior INTEIRO + a pergunta de agora.
    assert [m["role"] for m in vistas[1]] == ["system", "user", "assistant", "user"]
    assert vistas[1][1]["content"] == "meu cachorro chama Rex"
    assert vistas[1][2]["content"] == "entendi"
    assert vistas[1][3]["content"] == "qual a idade dele?"


@pytest.mark.asyncio
async def test_decidir_nao_entra_na_conversa(ia):
    """A armadilha desta mudanca.

    `decidir()` usa o MESMO modelo para escolher acoes do robo. Se aquilo
    caisse no historico, o proximo papo com o dono seria respondido no meio de
    deliberacao sobre avancar e virar - e a deliberacao seguinte viria
    contaminada pelo papo.
    """
    await ia.decidir({"frente_cm": 200}, ["avancar", "parar"])
    assert len(ia.historico) == 0

    await ia.responder("oi")
    assert len(ia.historico) == 1

    # E a decisao seguinte tambem nao pode VER o papo.
    await ia.decidir({"frente_cm": 200}, ["avancar", "parar"])
    papeis = [m["role"] for m in ia.capturado["mensagens"]]
    assert papeis == ["system", "user"]


# --------------------------------------------------------------------------
# Quando esquecer
# --------------------------------------------------------------------------


def test_conversa_expira_por_silencio():
    """Falar as 10h e voltar as 18h nao e a mesma conversa.

    Sem prazo, a pergunta da tarde chegaria colada num papo de oito horas
    atras - e o modelo responderia sobre aquilo. Pior que nao ter memoria,
    porque parece deliberado.
    """
    relogio = RelogioFalso()
    historico = HistoricoConversa(validade_s=600.0, relogio=relogio)
    historico.registrar("meu cachorro chama Rex", "que nome bonito")
    assert len(historico.mensagens()) == 2

    relogio.avancar(599)
    assert len(historico.mensagens()) == 2  # ainda e a mesma conversa

    relogio.avancar(2)
    assert historico.mensagens() == []
    assert len(historico) == 0


def test_conversa_expirada_nao_ressuscita():
    """Expirar tem que ESVAZIAR, nao so devolver vazio uma vez.

    Se o esquecimento fosse so no retorno, um segundo silencio mais curto
    faria a conversa velha voltar - o robo lembraria de novo de algo que ja
    tinha dado por encerrado.
    """
    relogio = RelogioFalso()
    historico = HistoricoConversa(validade_s=10.0, relogio=relogio)
    historico.registrar("a", "b")
    relogio.avancar(11)
    historico.mensagens()  # expira aqui
    historico.registrar("c", "d")
    assert [m["content"] for m in historico.mensagens()] == ["c", "d"]


def test_guarda_no_maximo_os_ultimos_turnos():
    historico = HistoricoConversa(max_turnos=2)
    for i in range(5):
        historico.registrar(f"pergunta {i}", f"resposta {i}")
    conteudos = [m["content"] for m in historico.mensagens()]
    assert conteudos == ["pergunta 3", "resposta 3", "pergunta 4", "resposta 4"]


def test_turno_pela_metade_nao_entra():
    """Historico terminando em `user` faria o modelo tratar a fala seguinte
    como continuacao de algo que ele nunca respondeu."""
    historico = HistoricoConversa()
    historico.registrar("pergunta sem resposta", "")
    historico.registrar("", "resposta sem pergunta")
    historico.registrar("   ", "   ")
    assert historico.mensagens() == []


def test_limpar_encerra_a_conversa():
    historico = HistoricoConversa()
    historico.registrar("a", "b")
    historico.limpar()
    assert historico.mensagens() == []


# --------------------------------------------------------------------------
# O caminho por frases (streaming) tambem lembra
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_guarda_a_resposta_INTEIRA():
    """A voz recebe frase a frase; o historico precisa do texto todo.

    Guardar so a primeira frase faria o modelo, no turno seguinte, achar que
    disse "Oi." e mais nada - e ele se corrigiria sozinho, do nada.
    """
    gerente = AiManager(provider="openai", openai_api_key="falsa")
    gerente._openai_em_frases = lambda mensagens: iter(
        ["Oi, tudo bem.", "Seu cachorro se chama Rex."]
    )

    frases = [frase async for frase in gerente.responder_em_frases("como ele chama?")]

    assert frases == ["Oi, tudo bem.", "Seu cachorro se chama Rex."]
    guardado = gerente.historico.mensagens()
    assert guardado[1]["content"] == "Oi, tudo bem. Seu cachorro se chama Rex."


@pytest.mark.asyncio
async def test_streaming_nao_registra_a_pergunta_duas_vezes():
    """Sem provedor remoto, `responder_em_frases` delega para `responder` - e
    `responder` ja registra sozinho. Registrar de novo na volta duplicaria a
    pergunta no historico."""
    gerente = AiManager(provider="ollama")
    gerente._chamar_ollama = lambda mensagens: "resposta unica"

    _ = [f async for f in gerente.responder_em_frases("oi")]

    assert len(gerente.historico) == 1
