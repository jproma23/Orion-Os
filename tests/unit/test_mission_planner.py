"""Testes do Mission Planner (Cap 7 s.4) - classificacao de
comando/pergunta, integracao com IA/memoria/hardware via fakes."""
import pytest

pytest.importorskip("ollama")

from orion.mission.mission_planner import MissionPlanner  # noqa: E402


def _coletor(destino: list):
    """Callback de hardware que so anota o que foi pedido."""

    async def enviar(comando):
        destino.append(comando)

    return enviar


class AiManagerFalso:
    def __init__(self, resposta: str = "resposta da IA") -> None:
        self.resposta = resposta
        self.chamadas: list[tuple[str, dict | None]] = []

    async def responder(self, texto_usuario: str, contexto: dict | None = None, **kwargs) -> str:
        self.chamadas.append((texto_usuario, contexto))
        return self.resposta


class MemoryClientFalso:
    def __init__(self, contexto: dict | None = None, notas: list[dict] | None = None) -> None:
        self._contexto = contexto or {}
        self._notas = notas
        self.lembrancas: list[tuple[str, dict]] = []
        self.consultas_de_nota: list[str] = []

    async def context(self, pessoa_id=None, limite_conversas=10, sobre=None):
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
async def test_comando_de_lanterna_aciona_hardware():
    """MUDOU EM 2026-08-03: quem reconhece o pedido e o modelo, nao uma regex.

    Antes este teste dizia tambem `ai.chamadas == []` - "comando reconhecido
    nao cai na IA". Hoje cai de proposito: e a IA quem entende o pedido, e o
    catalogo em codigo que decide o que ele faz. O que continua valendo, e e o
    que importa, e que o comando CHEGA ao hardware.
    """
    ai = AiManagerFalso(resposta="#acao:acender_luz\nPronto, acendi a lanterna.")
    comandos_enviados = []

    async def enviar(comando):
        comandos_enviados.append(comando)

    planner = MissionPlanner(ai_manager=ai, enviar_comando_hardware=enviar)

    resposta = await planner.processar("Fofao, acenda a lanterna")

    assert comandos_enviados == ["LIGHT_ON"]
    # O marcador nao pode sobrar no texto: ele viraria audio.
    assert "#acao" not in resposta
    assert resposta == "Pronto, acendi a lanterna."


@pytest.mark.asyncio
async def test_acao_sem_frase_depois_usa_a_resposta_do_catalogo():
    """Agir em silencio parece ter travado. Se o modelo so mandou a acao, o
    robo diz a frase pronta do catalogo em vez de ficar mudo."""
    ai = AiManagerFalso(resposta="#acao:acender_luz")
    planner = MissionPlanner(ai_manager=ai, enviar_comando_hardware=_coletor([]))

    resposta = await planner.processar("acende a luz")

    assert resposta == "Lanterna ligada."


@pytest.mark.asyncio
async def test_acao_inventada_pelo_modelo_nao_alcanca_o_hardware():
    """Regra 1 do contrato, no caminho falado: nome fora do catalogo nao vira
    chute. E o marcador some do texto assim mesmo - uma alucinacao do modelo
    nao pode virar ruido no alto-falante."""
    ai = AiManagerFalso(resposta="#acao:decolar\nJa estou voando.")
    comandos_enviados = []

    planner = MissionPlanner(
        ai_manager=ai, enviar_comando_hardware=_coletor(comandos_enviados)
    )
    resposta = await planner.processar("decola")

    assert comandos_enviados == []
    assert resposta == "Ja estou voando."


@pytest.mark.asyncio
async def test_pedido_de_cabeca_move_os_servos():
    """O buraco que motivou tudo isto (2026-08-03): nao havia como pedir
    "olha pra esquerda" por voz."""
    ai = AiManagerFalso(resposta="#acao:olhar_esquerda\nOlhando.")
    movimentos = []

    async def mover(pan, tilt):
        movimentos.append((pan, tilt))

    planner = MissionPlanner(ai_manager=ai, mover_cabeca=mover)
    resposta = await planner.processar("olha pra esquerda")

    assert movimentos == [(-60.0, 0.0)]
    assert resposta == "Olhando."


@pytest.mark.asyncio
async def test_cabeca_sem_fio_ligado_diz_que_nao_consegue():
    """Sem o callback de cabeca a acao existe no catalogo e nao tem caminho.
    O robo tem que DIZER - o modo de falha que este projeto mais repete e a
    acao nao acontecer e ninguem ficar sabendo."""
    ai = AiManagerFalso(resposta="#acao:olhar_cima")
    planner = MissionPlanner(ai_manager=ai)

    resposta = await planner.processar("olha pra cima")

    assert "consigo" in resposta.lower()


@pytest.mark.asyncio
async def test_parar_nao_passa_pela_ia():
    """PARAR e a UNICA ordem que continua por palavra-chave, e por isso
    importa testar que ela nao chama o modelo: se a internet cair com o robo
    andando, este e o caminho que sobra."""
    ai = AiManagerFalso()
    comandos_enviados = []

    planner = MissionPlanner(
        ai_manager=ai, enviar_comando_hardware=_coletor(comandos_enviados)
    )
    await planner.processar("pare agora")

    assert comandos_enviados == ["STOP"]
    assert ai.chamadas == []


@pytest.mark.asyncio
async def test_preparar_o_almoco_nao_para_o_robo():
    """A armadilha que o projeto ja tomou: "parar" casa DENTRO de "preparar".
    Os `\\b` do padrao sao o que impede o robo de obedecer a uma frase sobre o
    almoco."""
    comandos_enviados = []
    planner = MissionPlanner(
        ai_manager=AiManagerFalso(), enviar_comando_hardware=_coletor(comandos_enviados)
    )

    await planner.processar("voce sabe preparar o almoco?")

    assert comandos_enviados == []


@pytest.mark.asyncio
async def test_falha_ao_enviar_comando_retorna_mensagem_de_erro():
    ai = AiManagerFalso(resposta="#acao:acender_luz\nAcendendo.")

    async def enviar_com_erro(comando):
        raise RuntimeError("sem link com o hardware")

    planner = MissionPlanner(ai_manager=ai, enviar_comando_hardware=enviar_com_erro)
    resposta = await planner.processar("acenda a lanterna")

    assert "não consegui" in resposta.lower() or "nao consegui" in resposta.lower()


@pytest.mark.asyncio
async def test_contexto_e_passado_para_a_ia():
    ai = AiManagerFalso()
    memoria = MemoryClientFalso(contexto={"pessoa": {"nome": "Joao"}})
    planner = MissionPlanner(ai_manager=ai, memory_client=memoria)

    await planner.processar("qual seu nome", pessoa_id=5)

    contexto = ai.chamadas[0][1]
    assert contexto["pessoa"] == {"nome": "Joao"}


@pytest.mark.asyncio
async def test_o_catalogo_de_acoes_vai_sempre_no_contexto():
    """Sem banco, sem diario, sem familia: o robo tem que continuar sabendo o
    que sabe fazer. Um robo que so as vezes mexe a cabeca e pior que um que
    nunca mexeu - ninguem descobre a regra."""
    ai = AiManagerFalso()
    planner = MissionPlanner(ai_manager=ai)

    await planner.processar("oi")

    acoes = ai.chamadas[0][1]["acoes"]
    assert "olhar_esquerda" in acoes
    assert "acender_luz" in acoes


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
        async def context(self, pessoa_id=None, limite_conversas=10, sobre=None):
            raise RuntimeError("sem conexao com o Raspberry")

        async def remember(self, categoria, dados):
            raise RuntimeError("sem conexao com o Raspberry")

    planner = MissionPlanner(ai_manager=AiManagerFalso(resposta="oi"), memory_client=MemoriaQuebrada())
    resposta = await planner.processar("ola")

    assert resposta == "oi"


@pytest.mark.asyncio
async def test_apagar_e_acender_sao_acoes_diferentes_no_catalogo():
    """AS DUAS REGRESSOES DE 2026-07-19, guardadas onde elas agora moram.

    Elas existiam porque a deteccao era por substring: 'apague a lanterna' nao
    casava com `apag[ae]`, e 'desligue a luz' contem 'ligue a luz' - com
    LIGHT_ON testado primeiro, desligar LIGAVA a lanterna. A ordem dos padroes
    era o que segurava.

    Nao ha mais padrao nem ordem: sao dois nomes distintos de um catalogo
    fechado, e distinguir 'apague' de 'acenda' e trabalho de quem entende
    portugues. O que continua sendo nosso e cada nome levar ao comando certo -
    e e disso que este teste cuida.
    """
    from orion.mission.acoes_faladas import ACOES

    assert ACOES["apagar_luz"].comando == "LIGHT_OFF"
    assert ACOES["acender_luz"].comando == "LIGHT_ON"

    comandos_enviados = []
    planner = MissionPlanner(
        ai_manager=AiManagerFalso(resposta="#acao:apagar_luz\nApaguei."),
        enviar_comando_hardware=_coletor(comandos_enviados),
    )
    await planner.processar("Fofao, apague a lanterna")

    assert comandos_enviados == ["LIGHT_OFF"]


@pytest.mark.asyncio
async def test_recado_pedido_na_conversa_e_guardado():
    """A falha de 2026-08-03: o robo prometeu avisar a Mara e nao guardou
    nada. Aqui a promessa e o registro andam juntos ou nao andam."""
    ai = AiManagerFalso(
        resposta="#acao:deixar_recado Mara | o Joao Paulo quer falar com voce\n"
        "Combinado, aviso assim que eu a vir."
    )
    guardados = []

    async def guardar(para, texto, de):
        guardados.append((para, texto))
        return True

    planner = MissionPlanner(ai_manager=ai, deixar_recado=guardar)
    resposta = await planner.processar("avise a Mara que eu quero falar com ela")

    assert guardados == [("Mara", "o Joao Paulo quer falar com voce")]
    assert resposta == "Combinado, aviso assim que eu a vir."


@pytest.mark.asyncio
async def test_recado_que_nao_foi_guardado_NAO_pode_soar_como_combinado():
    """O CORACAO DESTE CONSERTO. Se o banco recusa, a frase do modelo diz
    'combinado, eu aviso' - e essa e exatamente a mentira que custou o recado
    da Mara. Quem esta ouvindo precisa saber que nao pode contar com isso."""
    ai = AiManagerFalso(resposta="#acao:deixar_recado Mara | oi\nCombinado, eu aviso!")

    async def recusar(para, texto, de):
        return False

    planner = MissionPlanner(ai_manager=ai, deixar_recado=recusar)
    resposta = await planner.processar("avise a Mara")

    assert "combinado" not in resposta.lower()
    assert "não consegui" in resposta.lower() or "nao consegui" in resposta.lower()


@pytest.mark.asyncio
async def test_recado_sem_destinatario_nao_e_guardado():
    """Sem o separador nao da para saber quem e o dono do recado, e adivinhar
    guardaria um recado para uma pessoa chamada 'Mara quero falar com ela'."""
    ai = AiManagerFalso(resposta="#acao:deixar_recado Mara quero falar\nCombinado!")
    guardados = []

    async def guardar(para, texto, de):
        guardados.append((para, texto))
        return True

    planner = MissionPlanner(ai_manager=ai, deixar_recado=guardar)
    resposta = await planner.processar("avise a Mara")

    assert guardados == []
    assert "combinado" not in resposta.lower()
