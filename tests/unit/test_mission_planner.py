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
    def __init__(self, contexto: dict | None = None) -> None:
        self._contexto = contexto or {}
        self.lembrancas: list[tuple[str, dict]] = []

    async def context(self, pessoa_id=None, limite_conversas=10):
        return self._contexto

    async def remember(self, categoria, dados):
        self.lembrancas.append((categoria, dados))
        return len(self.lembrancas)


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
async def test_comando_varredura_manda_scan_e_nao_chama_ia():
    ai = AiManagerFalso()
    comandos_enviados = []

    async def enviar(comando):
        comandos_enviados.append(comando)

    planner = MissionPlanner(ai_manager=ai, enviar_comando_hardware=enviar)
    resposta = await planner.processar("Fofao, faz uma varredura")

    assert comandos_enviados == ["SCAN_FRONT"]
    assert "varr" in resposta.lower()
    assert ai.chamadas == []  # comando reconhecido, nao cai na IA


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
