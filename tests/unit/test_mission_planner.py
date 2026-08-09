"""Testes do Mission Planner (Cap 7 s.4) - classificacao de
comando/pergunta, integracao com IA/memoria/hardware via fakes."""
import asyncio

import pytest

from orion.mission.mission_planner import (  # noqa: E402
    _PADRAO_PARAR,
    _PADRAO_VER_AGORA,
    MissionPlanner,
)


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
    def __init__(
        self,
        contexto: dict | None = None,
        notas: list[dict] | None = None,
        saberes: list[dict] | None = None,
    ) -> None:
        self._contexto = contexto or {}
        self._notas = notas
        self._saberes = saberes or []
        self.lembrancas: list[tuple[str, dict]] = []
        self.consultas_de_nota: list[str] = []
        self.consultas_ia = 0
        self.buscas_do_saber: list[str] = []

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

    def saber_plantar(self, titulo: str, corpo: str = "") -> None:
        self._saberes.append({"categoria": "conhecimentos", "titulo": titulo, "corpo": corpo})

    async def saber_buscar(self, consulta, limite=3):
        self.buscas_do_saber.append(consulta)
        return self._saberes

    async def registrar_consulta_ia(self):
        self.consultas_ia += 1


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


@pytest.mark.asyncio
async def test_conhecimento_ensinado_na_conversa_vai_para_o_vault():
    """"vamos inserir conhecimento pra ele" (2026-08-04). Ate aqui o vault
    tinha ZERO notas: `nota_escrever` funcionava e ninguem chamava."""
    ai = AiManagerFalso(
        resposta="#acao:guardar_conhecimento portao dos fundos | "
        "O portao dos fundos trava quando chove.\nGuardei."
    )
    guardados = []

    async def guardar(titulo, conteudo):
        guardados.append((titulo, conteudo))
        return True

    planner = MissionPlanner(ai_manager=ai, guardar_conhecimento=guardar)
    resposta = await planner.processar(
        "guarda isso: o portao dos fundos trava quando chove"
    )

    assert guardados == [
        ("portao dos fundos", "O portao dos fundos trava quando chove.")
    ]
    assert resposta == "Guardei."


@pytest.mark.asyncio
async def test_conhecimento_que_o_vault_recusou_nao_pode_soar_como_guardado():
    """Mesma regra do recado: "guardei" so pode ser dito se guardou. Aqui e
    ainda mais traicoeiro, porque quem ensinou vai perguntar semanas depois."""
    ai = AiManagerFalso(resposta="#acao:guardar_conhecimento x | y\nGuardei!")

    async def recusar(titulo, conteudo):
        return False

    planner = MissionPlanner(ai_manager=ai, guardar_conhecimento=recusar)
    resposta = await planner.processar("guarda isso")

    assert "guardei" not in resposta.lower()
    assert "não consegui" in resposta.lower() or "nao consegui" in resposta.lower()


# ---------------------------------------------------------------------------
# "PARA" E PREPOSICAO ANTES DE SER ORDEM (medido em 2026-08-05)
#
# O padrao era `\bpar[ae]\b`. Pedi ao robo, pelo chat: "deixa um recado PARA o
# Joao Paulo: a Marah pediu PARA ele ligar PARA a escola do Kamal". Ele
# respondeu "Parado.", mandou STOP ao Hardware Core e a frase NUNCA chegou na
# IA - o atalho de emergencia responde antes. O recado nao foi criado, e nada
# no log dizia que uma ordem tinha sido comida.
#
# Nao era so o recado: "vai para a cozinha", "olha para mim" e "liga para a
# escola" caiam no mesmo buraco. Um robo que para de andar toda vez que se diz
# "para" nao consegue receber quase nenhuma ordem em portugues.
# ---------------------------------------------------------------------------

ORDENS_DE_PARADA = [
    "para!",
    "para",
    "fofao para",
    "para tudo",
    "para agora",
    "para de andar",
    "para com isso",
    "pare",
    "pare agora",
    "parar",
    "stop",
    "PARA!",
]

FRASES_QUE_NAO_PARAM = [
    "deixa um recado para o Joao Paulo",
    "vai para a cozinha",
    "olha para mim",
    "liga para a escola do Kamal",
    "voce sabe preparar o almoco?",
    "isso e para voce",
    "conta uma historia para o Kamal",
    "a Marah pediu para ele ligar para a escola",
    "vira para a esquerda",
    "quero falar para a Mara",
]


@pytest.mark.parametrize("frase", ORDENS_DE_PARADA)
def test_ordem_de_parada_continua_parando(frase):
    """A emergencia nao pode ficar mais dificil de acionar do que era: se a
    rede cair com o robo andando, este e o unico caminho que sobra."""
    assert _PADRAO_PARAR.search(frase.lower()) is not None, frase


@pytest.mark.parametrize("frase", FRASES_QUE_NAO_PARAM)
def test_preposicao_para_nao_e_ordem_de_parada(frase):
    assert _PADRAO_PARAR.search(frase.lower()) is None, frase


@pytest.mark.asyncio
async def test_pedir_recado_chega_na_ia_em_vez_de_parar_o_robo():
    """O caso exato de 05/08, ponta a ponta: a frase tem que atravessar o
    atalho e chegar no modelo - senao nenhum recado com destinatario e
    possivel, porque todo destinatario vem depois de um "para"."""
    ai = AiManagerFalso()
    comandos_enviados = []
    planner = MissionPlanner(
        ai_manager=ai, enviar_comando_hardware=_coletor(comandos_enviados)
    )

    await planner.processar("deixa um recado para o Joao Paulo: a Marah quer falar com ele")

    assert comandos_enviados == [], "a frase virou parada de emergencia"
    assert ai.chamadas, "a frase nem chegou na IA"


# ---------------------------------------------------------------------------
# "QUE HORAS" NEM SEMPRE PERGUNTA O RELOGIO (medido em 2026-08-05)
#
# Mesmo defeito do `\bpar[ae]\b` acima, no atalho vizinho: era substring cru
# (`"que horas" in texto or "horario" in texto`). Perguntei ao robo ao vivo
# "que horas o Kamal chega da escola?" e ele respondeu "Agora sao 10 horas e 2
# minutos" - a pergunta nunca chegou na IA.
#
# O atalho tem razao de existir (o modelo nao sabe que horas sao), mas so vale
# quando a pergunta E o relogio. Seguida de sujeito ou verbo, a pergunta e
# sobre o mundo.
# ---------------------------------------------------------------------------

PERGUNTAS_DE_RELOGIO = [
    "que horas sao",
    "que horas são?",
    "que horas é",
    "que horas?",
    "fofao, que horas sao",
    "que horas e?",
    "que horas agora",
    "qual o horario atual",
]

PERGUNTAS_SOBRE_O_MUNDO = [
    "que horas o Kamal chega da escola",
    "que horas voce faz a ronda",
    "qual o horario do almoco",
    "me lembra do horario da escola",
    "que horas a Marah sai do trabalho",
    "voce sabe que horas o onibus passa",
]


@pytest.mark.parametrize("frase", PERGUNTAS_DE_RELOGIO)
def test_pergunta_de_relogio_continua_no_atalho(frase):
    assert MissionPlanner._eh_pergunta_de_hora(frase), frase


@pytest.mark.parametrize("frase", PERGUNTAS_SOBRE_O_MUNDO)
def test_pergunta_sobre_o_mundo_nao_vira_hora_do_relogio(frase):
    assert not MissionPlanner._eh_pergunta_de_hora(frase), frase


@pytest.mark.asyncio
async def test_quando_o_kamal_chega_da_escola_chega_na_ia():
    """O caso exato de 05/08: a pergunta e sobre a rotina da casa, e quem
    responde sobre a casa e a IA com o contexto - nao o relogio do sistema."""
    ai = AiManagerFalso()
    planner = MissionPlanner(ai_manager=ai)

    resposta = await planner.processar("que horas o Kamal chega da escola?")

    assert ai.chamadas, "a pergunta nem chegou na IA"
    assert "horas e" not in resposta, f"respondeu com o relogio: {resposta!r}"


# ---------------------------------------------------------------- O "Eu ja sei?"
# A memoria cognitiva (Camada 1, 2026-08-09): quando a fala COBRE um registro
# que ele guardou, a resposta sai do proprio acervo antes da IA - e, quando
# nao sabe, a IA e chamada e a consulta a ela e contada.
@pytest.mark.asyncio
async def test_quando_sabe_responde_do_proprio_acervo_sem_chamar_ia():
    ai = AiManagerFalso(resposta="Nao devia ter sido chamada")
    memoria = MemoryClientFalso()
    memoria.saber_plantar("Sensor de distancia", "Pingar, esperar o eco.")

    planner = MissionPlanner(ai_manager=ai, memory_client=memoria)
    resposta = await planner.processar("como mede a distancia?")

    assert "distancia" in resposta
    assert ai.chamadas == []
    assert memoria.consultas_ia == 0  # respondeu de casa, nao consultou a IA


@pytest.mark.asyncio
async def test_quando_nao_sabe_consulta_a_ia_e_conta():
    ai = AiManagerFalso(resposta="Vou pensar sobre isso.")
    memoria = MemoryClientFalso()  # nada plantado = saber_buscar devolve vazio

    planner = MissionPlanner(ai_manager=ai, memory_client=memoria)
    resposta = await planner.processar("o que e a teoria da relatividade?")

    assert resposta == "Vou pensar sobre isso."
    assert ai.chamadas
    assert memoria.buscas_do_saber, "a memoria nem foi consultada"
    # A contagem roda em tarefa de segundo plano (nao atrasa a fala) - o
    # teste deixa a fila drenar e a confere.
    await asyncio.sleep(0.05)
    assert memoria.consultas_ia == 1, "a consulta a IA nao foi contada"


@pytest.mark.asyncio
async def test_saber_busca_recebe_a_fala_inteira():
    ai = AiManagerFalso()
    memoria = MemoryClientFalso()
    memoria.saber_plantar("BME280", "Sensor de temperatura, pressao e umidade.")

    planner = MissionPlanner(ai_manager=ai, memory_client=memoria)
    await planner.processar("o que e um BME280?")

    assert memoria.buscas_do_saber == ["o que e um BME280?"]


@pytest.mark.asyncio
async def test_memoria_sem_fio_nao_emudece_vai_para_ia():
    """Se o Pi/banco esta fora, `saber_buscar` falha - e o erro nao pode
    derrubar a conversa: o degrau devolve "nao sei" e a IA responde."""
    ai = AiManagerFalso(resposta="Sem problema, posso ajudar.")

    class MemoriaFora(MemoryClientFalso):
        async def saber_buscar(self, consulta, limite=3):
            raise RuntimeError("SSD ausente")

    planner = MissionPlanner(ai_manager=ai, memory_client=MemoriaFora())
    resposta = await planner.processar("para que serve um BME280?")

    assert resposta == "Sem problema, posso ajudar."
    assert ai.chamadas


# ---------------------------------------------------------------------------
# "O QUE E ISSO?" - a visao atual consultavel (2026-08-09)
#
# O fio quente: perguntado o que ha na frente, o robo respondia do DIARIO
# (o passado) ou a IA chutava sem ver o frame. A resposta agora vem do
# Vision Core, que manteve o estado da ultima cena e o entrega por um
# callback consultado aqui. Mesma filosofia do diario: leitura de sensor,
# determinismo, e quem pergunta ouve o que a camera ve de verdade.
# ---------------------------------------------------------------------------

PERGUNTAS_VER_AGORA = [
    "o que e isso?",
    "o que é isso",
    "o que e esse",
    "o que e isto?",
    "fofao, o que e isso?",
    "o que tem na frente?",
    "o que ta na minha frente",
    "o que esta na frente?",
    "o que tem ai na frente?",
    "o que voce esta vendo?",
    "voce esta vendo o que?",
    "que objeto e esse?",
    "o que e esse objeto?",
    "o que e o objeto em minha mão?",
    "o que é o objeto na minha mão?",
    "o que e o objeto ai?",
    "o que e isso na minha mao?",
    "o que e isso ai?",
    "que coisa e essa?",
    "que e isto?",
    "o que é esse ai?",
]

FRASES_QUE_NAO_SAO_VER_AGORA = [
    "o que e isso que voce disse ontem?",
    "o que voce acha disso?",
    "o que voce esta vendo na televisao?",
    "deixa um recado para o Joao",
    "olha para frente",
    "que horas sao?",
    "o que teremos de almoco?",
    "o que e esse discurso?",
    "o que e o objeto de estudo da biologia?",
    "que objeto e esse da historia do livro?",
    "o que e isso que eu sinto por ela?",
]


@pytest.mark.parametrize("frase", PERGUNTAS_VER_AGORA)
def test_pergunta_do_que_ha_na_frente_casa_no_padrao(frase):
    assert _PADRAO_VER_AGORA.search(frase.lower()) is not None, frase


@pytest.mark.parametrize("frase", FRASES_QUE_NAO_SAO_VER_AGORA)
def test_pergunta_sem_padrao_nao_e_comida_pela_visao(frase):
    assert _PADRAO_VER_AGORA.search(frase.lower()) is None, frase


def _cena_fake(objetos=None, reconhecidos=None, desconhecidos=0):
    return {
        "objetos": objetos if objetos is not None else [{"classe": "chair", "confianca": 0.8}],
        "reconhecidos": reconhecidos if reconhecidos is not None else [],
        "desconhecidos": desconhecidos,
    }


@pytest.mark.asyncio
async def test_o_que_e_isso_responde_da_visao_sem_chamar_ia():
    ai = AiManagerFalso(resposta="Nao devia ter sido chamada")
    planner = MissionPlanner(
        ai_manager=ai,
        consultar_visao=lambda: _cena_fake(
            objetos=[
                {"classe": "chair", "confianca": 0.8},
                {"classe": "dog", "confianca": 0.9},
            ],
            reconhecidos=[{"nome": "Joao Paulo", "pessoa_id": 1}],
        ),
    )

    resposta = await planner.processar("o que e isso?")

    assert resposta == "Na minha frente agora estou vendo cadeira, cachorro, e o Joao Paulo."
    assert ai.chamadas == [], "leitura de sensor nao pode depender do modelo"


@pytest.mark.asyncio
async def test_o_que_e_isso_sem_cena_diz_que_nao_esta_vendo():
    ai = AiManagerFalso()
    planner = MissionPlanner(ai_manager=ai, consultar_visao=lambda: None)

    resposta = await planner.processar("o que tem na frente?")

    assert resposta == "Nao estou vendo nada na minha frente agora."
    assert ai.chamadas == []


@pytest.mark.asyncio
async def test_objeto_repetido_na_cena_e_falado_uma_vez():
    planner = MissionPlanner(
        ai_manager=AiManagerFalso(),
        consultar_visao=lambda: _cena_fake(objetos=[{"classe": "chair", "confianca": 0.7}] * 3),
    )

    resposta = await planner.processar("o que e isso?")

    assert resposta == "Na minha frente agora estou vendo cadeira."


@pytest.mark.asyncio
async def test_cena_so_com_desconhecido_diz_quem_nao_conhece():
    planner = MissionPlanner(
        ai_manager=AiManagerFalso(),
        consultar_visao=lambda: _cena_fake(objetos=[], desconhecidos=2),
    )

    resposta = await planner.processar("o que voce esta vendo?")

    assert resposta == "Na minha frente agora estou vendo alguem que eu nao conheco."


@pytest.mark.asyncio
async def test_o_que_e_isso_sem_visao_vai_para_a_ia():
    ai = AiManagerFalso(resposta="Isso e um enigma.")
    planner = MissionPlanner(ai_manager=ai)

    resposta = await planner.processar("o que e isso?")

    assert resposta == "Isso e um enigma."
    assert len(ai.chamadas) == 1


@pytest.mark.asyncio
async def test_pergunta_fora_do_padrao_nao_e_respondida_pela_visao():
    ai = AiManagerFalso(resposta="Sobre o almoco.")
    planner = MissionPlanner(ai_manager=ai, consultar_visao=lambda: _cena_fake())

    resposta = await planner.processar("o que teremos de almoco?")

    assert resposta == "Sobre o almoco."
    assert len(ai.chamadas) == 1


@pytest.mark.asyncio
async def test_visao_registrada_depois_passa_a_responder():
    """O fio nasce DEPOIS no boot (o planner vem antes da camera); registrar
    a visao no meio do dia tem que passar a responder de verdade."""
    ai = AiManagerFalso(resposta="vai para a IA")
    planner = MissionPlanner(ai_manager=ai)

    resposta_antes = await planner.processar("o que e isso?")
    assert resposta_antes == "vai para a IA"

    planner.registrar_consultar_visao(
        lambda: _cena_fake(objetos=[{"classe": "bird", "confianca": 0.9}])
    )

    resposta_depois = await planner.processar("o que e isso?")
    assert resposta_depois == "Na minha frente agora estou vendo passaro."
    # so a PRIMEIRA chamada foi a IA; depois da visao registrar, a segunda
    # pergunta saiu da camera e nao gastou modelo
    assert len(ai.chamadas) == 1
    assert ai.chamadas[0][0] == "o que e isso?"


@pytest.mark.asyncio
async def test_em_frases_tambem_responde_da_visao():
    planner = MissionPlanner(
        ai_manager=AiManagerFalso(),
        consultar_visao=lambda: _cena_fake(objetos=[{"classe": "cat", "confianca": 0.6}]),
    )

    respostas = [frase async for frase in planner.responder_em_frases("o que e isso?")]

    assert respostas == ["Na minha frente agora estou vendo gato."]
