"""Mission Planner (Cap 7 secao 4) - fluxo de decisao do Mission Core.

1. Receber evento (aqui: texto transcrito de um comando de voz, Cap 9).
2. Consultar contexto e memoria (MemoryClient -> comm.request, Fase 3).
3. Definir prioridade / classificar: comando de movimento, pergunta de
   hora (resposta direta e confiavel, sem depender do LLM "saber" a hora),
   ou pergunta geral (vai para a IA).
4. Consultar IA quando necessario.
5. Criar plano de acao (a resposta a dar / o comando a executar).
6. Enviar missao ao modulo apropriado (`enviar_comando_hardware`, tipicamente
   comm.send ao motion_core, que encaminha ao hardware_core - Cap 14 s.7).
7. Monitorar execucao (o callback injetado e quem aguarda o ACK/erro).
8. Registrar resultado (memory.remember em "conversas").

QUEM CLASSIFICA O PEDIDO (mudou em 2026-08-03). Ate aqui era palavra-chave:
sete expressoes regulares cobriam luz, parar, varrer, frente e os dois lados,
e o resto do que o robo sabe fazer era inalcancavel pela voz - a cabeca, entre
outros. Hoje quem entende a frase e o modelo, escolhendo um nome de catalogo
FECHADO (`acoes_faladas.py`); o que cada nome faz continua em codigo. O
"minimo" da Fase 6 virou o catalogo inteiro.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from collections.abc import Awaitable, Callable

from orion.mission.acoes_faladas import AcaoFalada
from orion.mission.acoes_faladas import bloco_para_prompt as _bloco_acoes
from orion.mission.acoes_faladas import extrair as _extrair_acao
from orion.mission.acoes_faladas import partir_argumento as _partir_argumento
from orion.mission.ai_manager import AiManager
from orion.mission.extrator_fatos import ExtratorFatos
from orion.mission.memory_client import MemoryClient

logger = logging.getLogger("orion.mission.mission_planner")

CallbackComandoHardware = Callable[[str], Awaitable[None]]
#: Move os servos da cabeca para um angulo absoluto (pan, tilt) em graus.
CallbackCabeca = Callable[[float, float], Awaitable[None]]
#: Guarda um recado (para_quem, texto, de_quem). Devolve se CONSEGUIU - e o
#: retorno importa: o robo so pode dizer "combinado" se algo foi de fato
#: guardado. Foi prometer sem guardar que criou este caminho inteiro.
CallbackRecado = Callable[[str, str, str], Awaitable[bool]]
#: Guarda um fato no vault (titulo, conteudo). Devolve se CONSEGUIU, pela
#: mesma razao do recado: "guardei" so pode ser dito se guardou.
CallbackConhecimento = Callable[[str, str], Awaitable[bool]]

#: "voce viu a Ana hoje?", "viu o Bruno?", "voce viu Ana por aqui?"
#: O nome vai ate o fim ou ate uma palavra de corte (hoje/por aqui/ai...).
_PADRAO_VIU_ALGUEM = re.compile(
    # o artigo exige espaco depois: sem isso o "a" de "alguem" era lido
    # como artigo e sobrava "lguem" como nome.
    r"\bviu\s+(?:(?:a|o|as|os)\s+)?(?P<nome>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,30}?)"
    r"(?:\s+(?:hoje|por\s+aqui|ai|aí|passar|passando|em\s+casa))?\s*[?.!]*$",
    re.IGNORECASE,
)


def _mesmo_nome(nome: str, descricao: str) -> str:
    """A descricao do diario e livre ("vi Joao Paulo"); casa pelo nome.

    Comparacao por prefixo do primeiro nome, sem acento e sem caixa: quem
    pergunta costuma dizer so o primeiro nome ("viu o Joao?") enquanto o
    diario guarda o nome completo cadastrado ("Joao Paulo").
    """
    import unicodedata

    def _normalizar(t: str) -> str:
        t = unicodedata.normalize("NFD", t.lower())
        return "".join(c for c in t if unicodedata.category(c) != "Mn")

    alvo = _normalizar(nome)
    texto = _normalizar(descricao)
    if not alvo:
        return False
    # casa nome inteiro ou primeiro nome dentro da descricao
    return alvo in texto or alvo.split()[0] in texto.split()


#: A UNICA ordem que ainda e reconhecida por palavra-chave, e a razao nao e
#: sobra do sistema antigo: **parar nao pode depender da internet**.
#:
#: Todo o resto do catalogo e escolhido pelo modelo remoto - o que e melhor
#: para entender o pedido e pior para garanti-lo. Se a rede cair no instante em
#: que alguem grita "para!" com o robo andando, esta linha e o que sobra. Ela
#: nao chama modelo nenhum, nao sai da maquina e responde no mesmo milissegundo.
#:
#: O `\b` nas duas pontas continua sendo o que impede o bug que o projeto ja
#: tomou: sem ele, "parar" casa dentro de "preparar" e o robo obedeceria a uma
#: frase sobre o almoco.
_PADRAO_PARAR = re.compile(r"\bpar[ae]\b|\bparar\b|\bstop\b")

#: O que ele diz ao parar. Texto pronto: este caminho existe justamente para
#: nao precisar de modelo, entao nao ha de onde vir uma frase melhor.
RESPOSTA_PARAR = "Parado."


class _SemCaminho(RuntimeError):
    """A acao existe no catalogo, mas nao ha fio ligado ate o hardware.

    Separada de uma falha de execucao de proposito: "nao consegui agora"
    (a serial caiu, o servo nao respondeu) e passageiro, e "ainda nao consigo"
    (o Mission Core subiu sem o callback) e configuracao. Dizer a mesma coisa
    nos dois casos manda procurar o problema no lugar errado.
    """


class _NaoGuardou(RuntimeError):
    """Prometeu-se um recado e ele nao foi guardado.

    Existe separada porque a resposta ao usuario tem que ser DIFERENTE: nas
    outras falhas o robo pode devolver a frase que o modelo escreveu, e aqui
    nao pode - a frase diz "combinado, eu aviso", e e justamente a promessa
    vazia que este caminho inteiro existe para nao repetir.
    """


class MissionPlanner:
    def __init__(
        self,
        ai_manager: AiManager,
        enviar_comando_hardware: CallbackComandoHardware | None = None,
        memory_client: MemoryClient | None = None,
        diario=None,
        familia: list[dict] | None = None,
        mover_cabeca: CallbackCabeca | None = None,
        deixar_recado: CallbackRecado | None = None,
        guardar_conhecimento: CallbackConhecimento | None = None,
    ) -> None:
        self._ai_manager = ai_manager
        self._enviar_comando_hardware = enviar_comando_hardware
        #: Quem move os servos da cabeca. Callback e nao `comm.send` direto de
        #: proposito: mover a cabeca aqui nao e so mandar SET_PAN_TILT - a
        #: visao mantem uma ESTIMATIVA da posicao dos servos e calcula a
        #: correcao seguinte a partir dela. Quem mexe na cabeca por fora tem
        #: que avisa-la, senao a proxima correcao sai de uma base que nao
        #: existe e a cabeca da um pulo ("o pan/tilt fica maluco depois de
        #: horas funcionando bem"). Quem monta o Mission Core e que sabe fazer
        #: as duas coisas; aqui so se pede.
        self._mover_cabeca = mover_cabeca
        #: Quem guarda os recados. Ver `orion/mission/recados.py` - e o modulo
        #: que existe porque o robo prometeu avisar a Mara e nao avisou.
        self._deixar_recado = deixar_recado
        #: Quem escreve no vault de longo prazo. Ate 2026-08-04 o vault tinha
        #: ZERO notas: `nota_escrever` funcionava ponta a ponta e nenhum
        #: codigo do robo o chamava. Este e o fio que faltava.
        self._guardar_conhecimento = guardar_conhecimento
        self._memory_client = memory_client
        self._extrator: ExtratorFatos | None = None
        # Diario das observacoes (camada 2). Sem ele o grounding recebe
        # lista vazia e o robo responde "nao tenho registro de hoje" - que
        # e correto, so que ele nunca teria registro de nada.
        self._diario = diario
        #: Quem sao as pessoas da casa e o que elas sao PARA ELE - vem da secao
        #: `familia` do orion.yaml (Joao Paulo e o criador, Kamal e como um
        #: irmao de 3 anos, Marah e como uma mae cuidadora).
        #:
        #: Ligado aqui em 2026-07-29 a pedido do dono ("poe contexto familiar
        #: para entender tudo"). A secao ja chegava a IA que DECIDE (no Pi) e
        #: NAO chegava a IA que CONVERSA - o robo decidia sabendo de quem se
        #: tratava e conversava sem saber, com a mesma pessoa, no mesmo minuto.
        self._familia = familia

    async def processar(self, texto_usuario: str, pessoa_id: int | None = None) -> str:
        contexto = await self._consultar_contexto(pessoa_id, texto_usuario)

        if self._pediu_para_parar(texto_usuario):
            resposta = await self._executar_comando("STOP", RESPOSTA_PARAR)
        elif self._eh_pergunta_de_hora(texto_usuario):
            resposta = self._responder_hora()
        elif (resposta_diario := await self._responder_sobre_quem_viu(
            texto_usuario, contexto
        )) is not None:
            resposta = resposta_diario
        else:
            bruta = await self._ai_manager.responder(texto_usuario, contexto)
            resposta = await self._cumprir_acao_pedida(bruta)

        await self._registrar_interacao(pessoa_id, texto_usuario, resposta)
        return resposta

    async def responder_em_frases(self, texto_usuario: str, pessoa_id: int | None = None):
        """Mesma decisao de `responder`, entregue FRASE A FRASE (T5, 2026-07-30).

        Existe para a fala poder comecar antes de a resposta terminar de ser
        gerada. Medido pareado, com o prompt real: 3,33 s -> 1,84 s ate o
        primeiro som (-45%).

        Os caminhos CURTOS (parar, pergunta de hora, resposta tirada do
        diario) nao passam pela IA: sao texto pronto, e saem numa frase so.
        Nao ha o que transmitir em pedacos, e picotar uma frase de seis
        palavras deixaria a fala pior, nao melhor.

        So o caminho da IA usa o streaming - e e justamente o unico que demora.
        """
        contexto = await self._consultar_contexto(pessoa_id, texto_usuario)

        if self._pediu_para_parar(texto_usuario):
            resposta = await self._executar_comando("STOP", RESPOSTA_PARAR)
        elif self._eh_pergunta_de_hora(texto_usuario):
            resposta = self._responder_hora()
        elif (resposta_diario := await self._responder_sobre_quem_viu(
            texto_usuario, contexto
        )) is not None:
            resposta = resposta_diario
        else:
            resposta = None

        if resposta is not None:
            yield resposta
            await self._registrar_interacao(pessoa_id, texto_usuario, resposta)
            return

        # O registro da interacao precisa do texto INTEIRO, e ele so existe
        # depois da ultima frase - por isso as partes sao acumuladas aqui em
        # vez de gravadas uma a uma. Gravar por frase encheria o historico de
        # fragmentos e quebraria o "conversas_recentes" que volta no contexto.
        partes = []
        primeira = True
        async for frase in self._ai_manager.responder_em_frases(texto_usuario, contexto):
            if primeira:
                # A ACAO SO PODE ESTAR AQUI. O marcador e a primeira coisa que
                # o modelo escreve (ver acoes_faladas), entao ele vem colado na
                # primeira frase - e e por isso que o servo comeca a se mexer
                # junto com o primeiro som, e nao depois da fala inteira.
                primeira = False
                frase = await self._cumprir_acao_pedida(frase)
                if not frase:
                    # A resposta era so o marcador. Nao ha o que falar ainda -
                    # e um yield vazio faria o sintetizador carregar o Piper
                    # para produzir silencio.
                    continue
            partes.append(frase)
            yield frase
        if partes:
            await self._registrar_interacao(pessoa_id, texto_usuario, " ".join(partes))

    async def _consultar_contexto(self, pessoa_id: int | None, texto_usuario: str) -> dict | None:
        contexto: dict = {}

        # O CATALOGO DE ACOES entra sempre, como a familia: nao depende de
        # rede, de banco nem de diario. Um robo que so as vezes sabe mexer a
        # cabeca e pior que um que nunca soube - ninguem descobre a regra.
        #
        # Gerado do codigo a cada consulta e nao guardado: e uma juncao de
        # strings, custa nada, e cache aqui so criaria uma segunda versao do
        # catalogo com prazo de validade proprio.
        contexto["acoes"] = _bloco_acoes()

        # A familia entra SEMPRE, e primeiro: nao depende de rede, de banco nem
        # de diario, e e o que faz ele falar com uma pessoa em vez de com um
        # usuario. Vem da config, entao nao ha por que arriscar perde-la numa
        # falha de qualquer uma das consultas abaixo.
        if self._familia:
            contexto["familia"] = self._familia

        if self._memory_client is not None:
            try:
                # `sobre=texto_usuario` fecha a ultima ponta da memoria de
                # longo prazo (2026-08-02). O texto ja estava aqui - este
                # metodo o recebe - e simplesmente nao era repassado, entao
                # o banco escolhia os fatos por DATA, sem saber do que a
                # pessoa estava falando.
                contexto.update(
                    await self._memory_client.context(pessoa_id, sobre=texto_usuario) or {}
                )
            except Exception:
                logger.exception("Falha ao consultar contexto da memoria")

        # As observacoes entram SEMPRE que houver diario, mesmo vazias: a
        # lista vazia e o que o grounding transforma em "nao tenho NENHUM
        # registro hoje", e e justamente isso que impede a IA de inventar
        # uma visita. Omitir o campo faria o silencio voltar.
        if self._diario is not None:
            try:
                contexto["observacoes"] = await self._diario.observacoes_de_hoje()
            except Exception:
                logger.exception("Falha ao ler o diario de observacoes")
                contexto["observacoes"] = []

        # Vault de conhecimento (EDR-0021) - opcional, tolera SSD ausente:
        # so enriquece o prompt, nunca impede a conversa de continuar.
        # A guarda de _memory_client nasceu no merge: antes a funcao saia
        # cedo quando nao havia memoria, agora ela segue em frente.
        if self._memory_client is not None:
            try:
                contexto["notas_relevantes"] = await self._memory_client.nota_buscar(texto_usuario)
            except Exception:
                logger.debug("Falha ao buscar notas do vault (SSD ausente?)", exc_info=True)

        return contexto or None

    @staticmethod
    def _pediu_para_parar(texto: str) -> bool:
        return _PADRAO_PARAR.search(texto.lower()) is not None

    async def _executar_comando(self, comando: str, resposta_pronta: str = "Feito.") -> str:
        if self._enviar_comando_hardware is not None:
            try:
                await self._enviar_comando_hardware(comando)
            except Exception:
                logger.exception("Falha ao enviar comando '%s' ao Hardware Core", comando)
                return "Nao consegui executar esse comando agora."
        return resposta_pronta

    async def _cumprir_acao_pedida(self, texto: str) -> str:
        """Faz o que o modelo pediu e devolve so o que deve ser FALADO.

        Sem marcador, devolve o texto intacto - que e o caso da maioria
        esmagadora das frases, porque conversa nao e pedido.

        A FALA TEM PREFERENCIA SOBRE A FRASE PRONTA do catalogo. O modelo
        escreveu "Claro, olhando pra esquerda agora" sabendo com quem esta
        falando e do que estavam falando; a `resposta` do catalogo nao sabe de
        nada disso. Ela so entra quando o modelo mandou a acao e nao escreveu
        nada depois - um robo que age em silencio parece um robo que travou.

        FALHAR AQUI NAO EMUDECE O ROBO: se o servo nao responde, ele diz que
        nao conseguiu. O modo de falha que este projeto mais repete e o
        contrario - a acao nao acontece e ninguem fica sabendo.
        """
        acao, argumento, falado = _extrair_acao(texto)
        if acao is None:
            return falado

        logger.info("acao falada: %s %s", acao.nome, argumento)
        try:
            if acao.entrega == "hardware":
                await self._despachar_comando(acao)
            elif acao.entrega == "cabeca":
                await self._despachar_cabeca(acao)
            elif acao.entrega == "recado":
                await self._despachar_recado(argumento)
            else:
                await self._despachar_conhecimento(argumento)
        except _SemCaminho as erro:
            logger.warning("acao %r pedida sem caminho ate o hardware: %s", acao.nome, erro)
            return falado or "Ainda nao consigo fazer isso."
        except _NaoGuardou:
            # NAO devolve `falado`: a frase do modelo diz "combinado, eu
            # aviso" ou "guardei", e essa e exatamente a mentira que este
            # caminho existe para nao repetir. Quem esta ouvindo precisa saber
            # que nao pode contar com isso.
            return "Desculpe, nao consegui guardar isso."
        except Exception:
            logger.exception("falha ao executar a acao %r", acao.nome)
            return "Nao consegui fazer isso agora."
        return falado or acao.resposta

    async def _despachar_comando(self, acao: AcaoFalada) -> None:
        if self._enviar_comando_hardware is None:
            raise _SemCaminho("sem callback de comando de hardware")
        await self._enviar_comando_hardware(acao.comando)

    async def _despachar_cabeca(self, acao: AcaoFalada) -> None:
        if self._mover_cabeca is None:
            raise _SemCaminho("sem callback de cabeca")
        await self._mover_cabeca(float(acao.pan or 0.0), float(acao.tilt or 0.0))

    async def _despachar_recado(self, argumento: str) -> None:
        if self._deixar_recado is None:
            raise _SemCaminho("sem callback de recado")
        para, mensagem = _partir_argumento(argumento)
        if not para:
            raise _NaoGuardou(f"argumento sem destinatario ou sem texto: {argumento!r}")
        if not await self._deixar_recado(para, mensagem, ""):
            raise _NaoGuardou(f"o banco recusou o recado para {para!r}")

    async def _despachar_conhecimento(self, argumento: str) -> None:
        if self._guardar_conhecimento is None:
            raise _SemCaminho("sem callback de conhecimento")
        titulo, conteudo = _partir_argumento(argumento)
        if not titulo:
            raise _NaoGuardou(f"argumento sem titulo ou sem conteudo: {argumento!r}")
        if not await self._guardar_conhecimento(titulo, conteudo):
            raise _NaoGuardou(f"o vault recusou a nota {titulo!r}")

    async def _responder_sobre_quem_viu(
        self, texto: str, contexto: dict | None
    ) -> str | None:
        """Responde "voce viu o fulano hoje?" consultando o diario.

        POR QUE ISTO NAO VAI PARA A IA
        ------------------------------
        Isto e consulta a banco, nao tarefa de linguagem - e modelo pequeno
        erra justamente aqui. Medido em 2026-07-19 com o diario contendo
        APENAS "09:12 vi Bruno", perguntado sobre a Ana:

            gemma3:1b -> "Sim, vi. as 09:12."
            gemma3:4b -> "Sim, eu vi a Ana as 09:12!"

        Os dois viram um registro com hora e responderam que sim, sem
        conferir DE QUEM era. Nenhuma redacao de prompt conserta isso de
        forma confiavel: com enfase em nao inventar, o 1b passou a negar ate
        o que ESTAVA no diario; com enfase em usar os fatos, passou a
        afirmar o que nao estava. Ele segue o tom da instrucao, nao os
        dados.

        Entao a resposta e montada aqui, por comparacao de nome, e a IA
        fica com o que ela faz bem: conversa livre.

        Devolve None quando a pergunta nao e desse tipo - ai segue o fluxo
        normal.
        """
        if self._diario is None:
            return None

        nome = self._extrair_nome_perguntado(texto)
        if nome is None:
            return None

        observacoes = (contexto or {}).get("observacoes") or []
        encontrados = [
            o for o in observacoes
            if _mesmo_nome(nome, str(o.get("o_que", "")))
        ]

        if not encontrados:
            return f"Nao, hoje eu nao vi {nome} por aqui."
        if len(encontrados) == 1:
            return f"Vi sim! Foi as {encontrados[0].get('quando', '?')}."
        horas = ", ".join(o.get("quando", "?") for o in encontrados)
        return f"Vi sim, mais de uma vez: {horas}."

    @staticmethod
    def _extrair_nome_perguntado(texto: str) -> str | None:
        """Pega o nome em perguntas do tipo "voce viu a Ana hoje?"."""
        casado = _PADRAO_VIU_ALGUEM.search(texto)
        if casado is None:
            return None
        nome = casado.group("nome").strip(" ?.!,")
        # "alguem"/"alguma pessoa" nao e nome proprio - deixa para a IA.
        if not nome or nome.lower() in {"alguem", "alguém", "alguma coisa", "algo"}:
            return None
        return nome

    @staticmethod
    def _eh_pergunta_de_hora(texto: str) -> bool:
        texto_normalizado = texto.lower()
        return "que horas" in texto_normalizado or "horario" in texto_normalizado

    @staticmethod
    def _responder_hora() -> str:
        agora = datetime.now()
        return f"Agora sao {agora.hour} horas e {agora.minute} minutos."

    @property
    def extrator(self) -> ExtratorFatos | None:
        """Criado sob demanda, e so quando ha memoria para escrever nele.

        Preguicoso de proposito: o planner e construido em varios caminhos
        (boot, testes, ferramentas de bancada) e nem todos tem memoria. Um
        extrator sem para onde gravar seria uma chamada de modelo jogada fora
        a cada quatro turnos.
        """
        if self._memory_client is None:
            return None
        if self._extrator is None:
            self._extrator = ExtratorFatos(self._ai_manager, self._memory_client)
        return self._extrator

    async def encerrar(self) -> None:
        """Fecha o planner: destila o que a ultima conversa deixou pendente.

        POR QUE EXISTE (2026-08-03): o extrator acumula turnos e so destila ao
        encher o lote ou apos silencio. No `Ctrl+C` nao ha nem uma coisa nem
        outra - e ate esta data ninguem chamava `encerrar()` nele, entao o
        resto do lote era descartado a cada desligamento. Com sessoes curtas
        (86% delas tem menos de 4 turnos) isso significava perder quase tudo.

        Usa o proprio `_extrator`, e nao a propriedade `extrator`: a
        propriedade CRIA um extrator sob demanda, e criar um extrator so para
        encerra-lo seria trabalho para nada.
        """
        if self._extrator is not None:
            await self._extrator.encerrar()

    async def _registrar_interacao(
        self, pessoa_id: int | None, texto_usuario: str, resposta: str
    ) -> None:
        if self._memory_client is None:
            return
        # A conversa vira FATO em segundo plano (2026-08-02). `agendar` volta
        # na hora: a extracao acontece depois, sem ninguem esperando. Perder um
        # fato e barato; atrasar a fala nao e.
        extrator = self.extrator
        if extrator is not None:
            extrator.agendar(texto_usuario, resposta)
        try:
            await self._memory_client.remember(
                "conversas", {"pessoa_id": pessoa_id, "papel": "usuario", "texto": texto_usuario}
            )
            await self._memory_client.remember(
                "conversas", {"pessoa_id": pessoa_id, "papel": "robo", "texto": resposta}
            )
        except Exception:
            logger.exception("Falha ao registrar conversa na memoria")
