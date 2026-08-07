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
from collections.abc import Awaitable, Callable
from datetime import datetime

from orion.ensino.banco_aulas import normalizar_nivel as _normalizar_nivel
from orion.mission.acoes_faladas import MARCADOR, AcaoFalada
from orion.mission.acoes_faladas import bloco_para_prompt as _bloco_acoes
from orion.mission.acoes_faladas import extrair as _extrair_acao
from orion.mission.acoes_faladas import partir_argumento as _partir_argumento
from orion.mission.ai_manager import SEM_IA, AiManager
from orion.mission.comando_local import casar as _casar_comando_local
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
#:
#: "PARA" SOZINHO NAO BASTA (medido em 2026-08-05). O padrao era
#: `\bpar[ae]\b`, e "para" e a preposicao mais comum do portugues: *"deixa um
#: recado PARA o Joao Paulo"* virou STOP, os motores pararam e a frase nunca
#: chegou na IA - o atalho responde "Parado." ANTES de o modelo ver o pedido.
#: Junto com ela caiam "vai para a cozinha", "olha para mim", "liga para a
#: escola": ordens legitimas comidas pelo caminho de emergencia.
#:
#: Entao "para" so vale como ordem quando esta POSICIONADO como ordem - no fim
#: da fala ("fofao, para!") ou seguido do que segue um imperativo ("para
#: tudo", "para de andar", "para com isso"). Como preposicao ele sempre vem
#: seguido do destino ("para o Joao", "para a cozinha", "para mim"), e ai nao
#: para mais nada.
#:
#: `pare`, `parar`, `para` e `stop` continuam parando SEMPRE, em qualquer
#: posicao: nenhuma das quatro e ambigua em portugues, e a emergencia nao pode
#: ficar mais dificil de acionar do que era. So a forma ambigua ganhou regra.
_PADRAO_PARAR = re.compile(
    r"\bpare\b"
    r"|\bparar\b"
    r"|\bpára\b"
    r"|\bstop\b"
    r"|\bpara\b(?=[\s!.,;?]*$|\s+(?:tudo|ja|já|agora|ai|aí|de|com)\b)"
)

#: O que ele diz ao parar. Texto pronto: este caminho existe justamente para
#: nao precisar de modelo, entao nao ha de onde vir uma frase melhor.
RESPOSTA_PARAR = "Parado."

#: Quando a frase e um pedido de AULA (2026-08-07). Palavra inteira, sempre:
#: sem `\b`, "aula" casaria dentro de "Paula" e "curso" dentro de "percurso" -
#: e a deteccao por substring e o defeito que este projeto ja tomou.
#:
#: Exige uma palavra que so aparece em pedido de ensino. "me explica" ficou de
#: FORA de proposito: e frase de conversa normal, e sequestra-la faria o robo
#: responder com uma aula de sete minutos a quem so fez uma pergunta.
_PADRAO_AULA = re.compile(
    r"\baulas?\b"
    r"|\bcursos?\b"
    r"|\bme ensin[ae]\b"
    r"|\bquero aprender\b"
    r"|\bpode me ensinar\b"
)

#: Como ele anuncia o nivel na frase de abertura.
_ARTIGO_NIVEL = {
    "basico": "basica",
    "intermediario": "intermediaria",
    "avancado": "avancada",
}

#: "QUE HORAS" NEM SEMPRE PERGUNTA O RELOGIO (medido em 2026-08-05).
#:
#: Era `"que horas" in texto or "horario" in texto` - substring cru, sem
#: fronteira e sem posicao, o mesmo defeito de `\bpar[ae]\b` consertado horas
#: antes neste arquivo. Medido ao vivo: *"que horas o Kamal chega da escola?"*
#: foi respondido com **"Agora sao 10 horas e 2 minutos"** e a pergunta nunca
#: chegou na IA. Junto com ela caiam "que horas voce faz a ronda?" e, por causa
#: do segundo termo, qualquer frase com "horario" - "qual o horario do almoco",
#: "me lembra do horario da escola".
#:
#: O atalho existe por um bom motivo (a hora nao precisa de modelo, e o modelo
#: nao sabe que horas sao), mas ele so vale quando a pergunta E o relogio:
#: "que horas sao/e" ou "que horas?" sozinho no fim. Seguido de qualquer outra
#: coisa - de um sujeito, de um verbo - a pergunta e sobre o MUNDO, e quem
#: responde sobre o mundo e a IA com o contexto na mao.
_PADRAO_HORA = re.compile(
    r"\bque horas?\s*(?:sao|são|eh|é)\b"
    r"|\bque horas\s+e\s*[?!.]*$"
    r"|\bque horas\s*[?!.]*$"
    r"|\bhoras agora\b"
    r"|\bhor[áa]rio atual\b"
)


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
        consultar_internet=None,
        professor=None,
        autoconhecimento=None,
        ementas=None,
    ) -> None:
        #: Responde "o que voce ensina?" a partir do mapa das ementas.
        self._ementas = ementas
        #: O que ele sabe sobre o proprio corpo (2026-08-07). None = ele nao
        #: responde sobre si e a pergunta vai para a IA - que vai inventar.
        self._autoconhecimento = autoconhecimento
        #: Quem da aula a partir do banco local (2026-08-07). None = o robo
        #: nao da aula e o pedido segue para a conversa normal. Mesmo
        #: contrato dos outros callbacks: sem caminho, ele nao finge.
        self._professor = professor
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
        #: Consulta a internet (2026-08-07). None = o robo nao busca nada e
        #: a acao responde que nao consegue - mesmo contrato dos outros
        #: callbacks: sem caminho, ele DIZ que nao consegue.
        self._consultar_internet = consultar_internet
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
            # "para" tambem encerra a aula. Sem isto, o robo pararia as rodas
            # e continuaria dando aula - obedecendo pela metade, que e pior
            # que nao obedecer, porque parece que ele ignorou.
            if self._professor is not None:
                await self._professor.parar()
            resposta = await self._executar_comando("STOP", RESPOSTA_PARAR)
        elif (aula := await self._tentar_dar_aula(texto_usuario)) is not None:
            resposta = aula
        elif (programa := self._responder_ementa(texto_usuario)) is not None:
            resposta = programa
        elif (sobre_mim := self._responder_sobre_mim(texto_usuario)) is not None:
            resposta = sobre_mim
        elif self._eh_pergunta_de_hora(texto_usuario):
            resposta = self._responder_hora()
        elif (resposta_diario := await self._responder_sobre_quem_viu(
            texto_usuario, contexto
        )) is not None:
            resposta = resposta_diario
        else:
            bruta = await self._ai_manager.responder(texto_usuario, contexto)
            # Mesmo degrau do caminho por frases: se a IA nao respondeu, ele
            # tenta obedecer sozinho antes de dizer que nao consegue pensar.
            if bruta == SEM_IA and (local := await self._obedecer_sem_ia(texto_usuario)):
                resposta = local
            else:
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
            # "para" tambem encerra a aula. Sem isto, o robo pararia as rodas
            # e continuaria dando aula - obedecendo pela metade, que e pior
            # que nao obedecer, porque parece que ele ignorou.
            if self._professor is not None:
                await self._professor.parar()
            resposta = await self._executar_comando("STOP", RESPOSTA_PARAR)
        elif (aula := await self._tentar_dar_aula(texto_usuario)) is not None:
            resposta = aula
        elif (programa := self._responder_ementa(texto_usuario)) is not None:
            resposta = programa
        elif (sobre_mim := self._responder_sobre_mim(texto_usuario)) is not None:
            resposta = sobre_mim
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
                # A IA NAO RESPONDEU (cota, chave, rede). Antes de repetir
                # "nao consigo pensar", tenta obedecer sozinho: o catalogo
                # inteiro estava trancado atras do modelo, e foi assim que a
                # cota esgotada deixou o robo sem fazer NADA em 07/08.
                if frase == SEM_IA:
                    local = await self._obedecer_sem_ia(texto_usuario)
                    if local is not None:
                        yield local
                        await self._registrar_interacao(pessoa_id, texto_usuario, local)
                        return
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

    def _responder_ementa(self, texto: str) -> str | None:
        """"O que voce ensina?" - respondido do mapa, sem modelo.

        Diz o que ele sabe E o que ainda nao sabe, com numero. Um robo que
        listasse as 312 entradas do programa como se soubesse todas estaria
        mentindo; a lista honesta e mais util, porque diz o que dá para pedir
        hoje.
        """
        if self._ementas is None:
            return None
        try:
            return self._ementas(texto)
        except Exception:
            logger.exception("falha ao consultar as ementas")
            return None

    def _responder_sobre_mim(self, texto: str) -> str | None:
        """O que ele sabe do proprio corpo - antes da IA, e nao depois.

        Diferente do comando local, este caminho NAO e um substituto para
        quando falta cota: e a resposta certa mesmo com a IA no ar. O modelo
        nao conhece este hardware e, perguntado sobre o proprio corpo, ele
        inventa - ja chegou a este projeto um catalogo que dava ao robo uma
        acao de "pegar" e "trazer", e o robo NAO TEM BRACOS.
        """
        if self._autoconhecimento is None:
            return None
        try:
            return self._autoconhecimento.responder(texto)
        except Exception:
            # Banco ausente ou corrompido nao pode emudecer a conversa: ele
            # so perde essa resposta e segue para a IA.
            logger.exception("falha ao consultar o autoconhecimento")
            return None

    async def _obedecer_sem_ia(self, texto: str) -> str | None:
        """Executa a acao pedida quando a IA NAO RESPONDEU. None = nao deu.

        Roda so depois de o modelo falhar (cota esgotada, sem chave, rede
        fora). Com a IA no ar, nada disto e consultado - o degrau e ABAIXO
        dela, nao acima, e e isso que o separa do classificador de intencao
        que este projeto recusou.

        O casador escreve O MESMO MARCADOR que o modelo escreveria, e a
        execucao segue pelo caminho de sempre: um caminho so ate o hardware,
        ja testado, em vez de uma segunda maneira de acionar as mesmas acoes
        que precisaria ser mantida em paralelo.
        """
        casamento = _casar_comando_local(texto)
        if casamento is None:
            return None
        marcador = f"{MARCADOR}{casamento.acao.nome}"
        if casamento.argumento:
            marcador += f" {casamento.argumento}"
        return await self._cumprir_acao_pedida(marcador)

    async def _tentar_dar_aula(self, texto: str) -> str | None:
        """Da aula a partir do BANCO LOCAL, sem chamar modelo nenhum.

        Este e o primeiro degrau da cascata que o dono desenhou em
        2026-08-07: o conteudo esta em arquivo, o robo le. Nao gasta cota,
        nao muda a cada vez, e funciona sem internet - e dar aula seria
        justamente o uso que mais gastaria cota.

        `None` significa "nao e pedido de aula" OU "nao tenho essa aula", e
        nos dois casos a frase segue o caminho normal, que termina na IA. E
        de proposito que os dois casos se parecem aqui: quem decide o que
        fazer quando o banco nao sabe e a cascata inteira, nao este metodo.

        O gatilho e por PALAVRA INTEIRA (`\\b`), e nao por substring. Este
        projeto ja tomou esse defeito: "parar" casa dentro de "preparar", e
        e o motivo registrado para o classificador de intencao ter sido
        recusado. Aqui, sem o limite de palavra, "aula" casaria dentro de
        "Paula".
        """
        if self._professor is None:
            return None
        if not _PADRAO_AULA.search(texto):
            return None
        nivel = _normalizar_nivel(texto)
        aula = self._professor.procurar(texto, nivel=nivel)
        if aula is None:
            # NAO devolve None aqui: era pedido de aula, e mandar para a IA
            # faria o robo dizer "nao consigo pensar" a um pedido que ele
            # sabe responder - ele sabe QUE NAO TEM, e sabe o que tem.
            #
            # Nasceu de um caso real (07/08): "me da uma aula de ciencia"
            # devolvia a aula de equacao do segundo grau. Dizer que nao tem,
            # e oferecer o que tem, e melhor que as duas coisas.
            logger.info("pedido de aula sem aula no banco: %r", texto)
            return self._professor.oferecer_o_que_tem()
        await self._professor.dar_aula(aula)
        # A frase de abertura vem daqui e nao do banco: ela precisa dizer o
        # nivel que ele escolheu, porque pode nao ser o que a pessoa pediu -
        # quem pede "avancado" e recebe "basico" tem que saber disso.
        return f"Vou dar uma aula {_ARTIGO_NIVEL[aula.nivel]} sobre {aula.titulo}. Olha o quadro."

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
            elif acao.entrega == "internet":
                # SAI AQUI, e a saida antecipada e o ponto: nas outras acoes
                # a fala do modelo vence (`falado or acao.resposta`), porque
                # ele sabe do contexto e a acao nao. Na busca e o INVERSO -
                # o achado E a resposta, e o modelo nao o conhecia quando
                # escreveu a frase. Deixar `falado` vencer aqui faria o robo
                # dizer o que ele imaginou em vez do que leu.
                return await self._despachar_busca(argumento)
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

    async def _despachar_busca(self, termo: str) -> str:
        """Consulta a internet e devolve o que sera FALADO, com a fonte.

        Uma chamada ao modelo, nao duas: o achado ja vem redigido e vira a
        resposta direto. O caminho de pedir ao modelo que reescreva o
        resultado custaria o dobro da cota, e a cota deste projeto acabou
        as 10h da manha de 07/08.
        """
        if self._consultar_internet is None:
            raise _SemCaminho("sem callback de consulta a internet")
        achado = await self._consultar_internet(termo)
        if achado is None:
            # NAO devolve a frase do modelo: ela foi escrita supondo que a
            # busca daria certo. Dizer que nao achou e a verdade.
            #
            # E DIZ O QUE PROCUROU. Sem isso, "procurei e nao encontrei" e
            # indistinguivel de "entendi errado o que voce falou" - e as duas
            # coisas aconteceram em uso real no mesmo dia, uma delas com o
            # Whisper transcrevendo a frase errada. Falando o termo, quem
            # perguntou conserta na hora, repetindo de outro jeito.
            return f"Procurei sobre {termo} e nao encontrei nada na Wikipedia."
        return achado.para_falar()

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
        return _PADRAO_HORA.search(texto.lower()) is not None

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
