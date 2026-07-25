"""Testes do Mission Planner (Cap 7 s.4) - classificacao de
comando/pergunta, integracao com IA/memoria/hardware via fakes."""
import pytest

pytest.importorskip("ollama")

from orion.mission.mission_planner import MissionPlanner  # noqa: E402


class AiManagerFalso:
    def __init__(self, resposta: str = "resposta da IA") -> None:
        self.resposta = resposta
        self.chamadas: list[tuple[str, dict | None]] = []

    async def responder(self, texto_usuario: str, contexto: dict | None = None) -> str:
        self.chamadas.append((texto_usuario, contexto))
        return self.resposta


class MemoryClientFalso:
    def __init__(self, contexto: dict | None = None, notas: list[dict] | None = None) -> None:
        self._contexto = contexto or {}
        self._notas = notas
        self.lembrancas: list[tuple[str, dict]] = []
        self.consultas_de_nota: list[str] = []

    async def context(self, pessoa_id=None, limite_conversas=10):
        return self._contexto

    async def remember(self, categoria, dados):
        self.lembrancas.append((categoria, dados))
        return len(self.lembrancas)

    async def nota_buscar(self, consulta, limite=5):
        self.consultas_de_nota.append(consulta)
        if self._notas is None:
            raise RuntimeError("vault indisponivel (SSD ausente)")
        return self._notas


@pytest.mark.asyncio
async def test_pergunta_de_hora_responde_direto_sem_chamar_ia():
    ai = AiManagerFalso()
    planner = MissionPlanner(ai_manager=ai)

    resposta = await planner.processar("Fofao, que horas sao?")

    assert "horas" in resposta
    assert ai.chamadas == []  # nao deveria ter chamado a IA


@pytest.mark.asyncio
async def test_pergunta_geral_vai_para_ia():
    ai = AiManagerFalso(resposta="Eu sou o Fofão.")
    planner = MissionPlanner(ai_manager=ai)

    resposta = await planner.processar("quem e voce")

    assert resposta == "Eu sou o Fofão."
    assert len(ai.chamadas) == 1


@pytest.mark.asyncio
async def test_comando_de_lanterna_aciona_hardware_e_nao_chama_ia():
    ai = AiManagerFalso()
    comandos_enviados = []

    async def enviar(comando):
        comandos_enviados.append(comando)

    planner = MissionPlanner(ai_manager=ai, enviar_comando_hardware=enviar)

    resposta = await planner.processar("Fofao, acenda a lanterna")

    assert comandos_enviados == ["LIGHT_ON"]
    assert "lanterna" in resposta.lower()
    assert ai.chamadas == []


@pytest.mark.asyncio
async def test_comando_stop():
    comandos_enviados = []

    async def enviar(comando):
        comandos_enviados.append(comando)

    planner = MissionPlanner(ai_manager=AiManagerFalso(), enviar_comando_hardware=enviar)
    await planner.processar("pare agora")

    assert comandos_enviados == ["STOP"]


@pytest.mark.asyncio
async def test_falha_ao_enviar_comando_retorna_mensagem_de_erro():
    async def enviar_com_erro(comando):
        raise RuntimeError("sem link com o hardware")

    planner = MissionPlanner(ai_manager=AiManagerFalso(), enviar_comando_hardware=enviar_com_erro)
    resposta = await planner.processar("acenda a lanterna")

    assert "não consegui" in resposta.lower() or "nao consegui" in resposta.lower()


@pytest.mark.asyncio
async def test_contexto_e_passado_para_a_ia():
    ai = AiManagerFalso()
    memoria = MemoryClientFalso(contexto={"pessoa": {"nome": "Joao"}})
    planner = MissionPlanner(ai_manager=ai, memory_client=memoria)

    await planner.processar("qual seu nome", pessoa_id=5)

    assert ai.chamadas[0][1] == {"pessoa": {"nome": "Joao"}}


@pytest.mark.asyncio
async def test_interacao_e_registrada_na_memoria():
    memoria = MemoryClientFalso()
    planner = MissionPlanner(ai_manager=AiManagerFalso(resposta="oi"), memory_client=memoria)

    await planner.processar("ola", pessoa_id=3)

    assert len(memoria.lembrancas) == 2
    categoria_usuario, dados_usuario = memoria.lembrancas[0]
    assert categoria_usuario == "conversas"
    assert dados_usuario["papel"] == "usuario"
    assert dados_usuario["texto"] == "ola"
    assert memoria.lembrancas[1][1]["papel"] == "robo"


@pytest.mark.asyncio
async def test_notas_do_vault_sao_adicionadas_ao_contexto():
    ai = AiManagerFalso()
    notas = [{"titulo": "Aniversario da Marah", "trecho": "12 de marco"}]
    memoria = MemoryClientFalso(contexto={"pessoa": {"nome": "Joao"}}, notas=notas)
    planner = MissionPlanner(ai_manager=ai, memory_client=memoria)

    await planner.processar("quando e o aniversario da Marah")

    assert ai.chamadas[0][1]["notas_relevantes"] == notas
    assert memoria.consultas_de_nota == ["quando e o aniversario da Marah"]


@pytest.mark.asyncio
async def test_vault_indisponivel_nao_impede_resposta_da_ia():
    """Sem vault (SSD ausente), a conversa segue normalmente - so sem as
    notas de longo prazo no contexto (Cap 6 s.8, EDR-0021)."""
    ai = AiManagerFalso(resposta="tudo bem")
    memoria = MemoryClientFalso(contexto={"pessoa": {"nome": "Joao"}})  # notas=None
    planner = MissionPlanner(ai_manager=ai, memory_client=memoria)

    resposta = await planner.processar("oi")

    assert resposta == "tudo bem"
    assert "notas_relevantes" not in ai.chamadas[0][1]


@pytest.mark.asyncio
async def test_falha_na_memoria_nao_derruba_o_processamento():
    class MemoriaQuebrada:
        async def context(self, pessoa_id=None, limite_conversas=10):
            raise RuntimeError("sem conexao com o Raspberry")

        async def remember(self, categoria, dados):
            raise RuntimeError("sem conexao com o Raspberry")

    planner = MissionPlanner(ai_manager=AiManagerFalso(resposta="oi"), memory_client=MemoriaQuebrada())
    resposta = await planner.processar("ola")

    assert resposta == "oi"


@pytest.mark.asyncio
async def test_apague_no_subjuntivo_desliga_a_lanterna():
    """Regressao (2026-07-19): 'apague a lanterna' caia na IA - o padrao
    antigo apag[ae] nao cobria o subjuntivo 'apague'."""
    ai = AiManagerFalso()
    comandos_enviados = []

    async def enviar(comando):
        comandos_enviados.append(comando)

    planner = MissionPlanner(ai_manager=ai, enviar_comando_hardware=enviar)
    await planner.processar("Fofao, apague a lanterna")

    assert comandos_enviados == ["LIGHT_OFF"]
    assert ai.chamadas == []


@pytest.mark.asyncio
async def test_desligue_a_luz_nao_liga_a_lanterna():
    """Regressao (2026-07-19): 'desligue a luz' contem 'ligue a luz' como
    substring - com LIGHT_ON testado primeiro, desligar LIGARIA a
    lanterna. A ordem dos padroes (OFF antes de ON) protege disso."""
    ai = AiManagerFalso()
    comandos_enviados = []

    async def enviar(comando):
        comandos_enviados.append(comando)

    planner = MissionPlanner(ai_manager=ai, enviar_comando_hardware=enviar)
    await planner.processar("desligue a luz")

    assert comandos_enviados == ["LIGHT_OFF"]
    assert ai.chamadas == []
