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

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from orion.ensino.banco_aulas import normalizar_nivel as _normalizar_nivel
from orion.mission.acoes_faladas import MARCADOR, AcaoFalada
from orion.mission.acoes_faladas import bloco_para_prompt as _bloco_acoes
from orion.mission.acoes_faladas import extrair as _extrair_acao
from orion.mission.acoes_faladas import parametros_do_comando as _parametros_do_comando
from orion.mission.acoes_faladas import partir_argumento as _partir_argumento
from orion.mission.ai_manager import SEM_IA, AiManager
from orion.mission.comando_local import casar as _casar_comando_local
from orion.mission.comando_local import combina_com_acao as _combina_com_acao
from orion.mission.comando_local import pediu_de_fato as _pediu_de_fato
from orion.mission.comando_local import pediu_para_guardar as _pediu_para_guardar
from orion.mission.extrator_fatos import ExtratorFatos
from orion.mission.memory_client import MemoryClient

from orion.mission.jarvis_plugins import get_registry, execute_plugin
logger = logging.getLogger("orion.mission.mission_planner")

#: Manda um COMMAND ao firmware: `(comando)` ou `(comando, parametros)`. Os
#: parametros (2026-10-31) carregam a MEDIDA do movimento - distancia_cm,
#: graus, velocidade. Sem eles "anda para frente" andava sem fim.
CallbackComandoHardware = Callable[..., Awaitable[None]]
#: O corpo AGORA, em texto para o modelo ler: o que o LiDAR ve em volta, a
#: bussola e o que o ultimo movimento fez de verdade. None = nada a dizer.
CallbackEstadoCorpo = Callable[[], Awaitable[str | None]]
#: Move os servos da cabeca para um angulo absoluto (pan, tilt) em graus.
CallbackCabeca = Callable[[float, float], Awaitable[None]]
#: Guarda um recado (para_quem, texto, de_quem). Devolve se CONSEGUIU - e o
#: retorno importa: o robo so pode dizer "combinado" se algo foi de fato
#: guardado. Foi prometer sem guardar que criou este caminho inteiro.
CallbackRecado = Callable[[str, str, str], Awaitable[bool]]
#: Guarda um fato no vault (titulo, conteudo). Devolve se CONSEGUIU, pela
#: mesma razao do recado: "guardei" so pode ser dito se guardou.
CallbackConhecimento = Callable[[str, str], Awaitable[bool]]
#: O que a camera ve AGORA (dict do VisionCore.ver_agora) ou None.
#:
#: Sincrono de proposito: ler o estado da ultima cena e uma leitura em
#: memoria, sem rede nem serial - nada para esperar. Os outros callbacks
#: deste planner sao async porque fazem I/O; este nao faz.
CallbackVisao = Callable[[], dict | None]

#: "voce viu a Ana hoje?", "viu o Bruno?", "voce viu Ana por aqui?"
#: O nome vai ate o fim ou ate uma palavra de corte (hoje/por aqui/ai...).
_PADRAO_VIU_ALGUEM = re.compile(
    # o artigo exige espaco depois: sem isso o "a" de "alguem" era lido
    # como artigo e sobrava "lguem" como nome.
    r"\bviu\s+(?:(?:a|o|as|os)\s+)?(?P<nome>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,30}?)"
    r"(?:\s+(?:hoje|por\s+aqui|ai|aí|passar|passando|em\s+casa))?\s*[?.!]*$",
    re.IGNORECASE,
)


#: Ate quantas palavras uma frase pode ter para ser tratada como NARRACAO da
#: acao, e nao como conteudo. Oito: "Varrendo a frente aqui para ver o
#: movimento." tem oito e e narracao; passar disso ja e resposta.
_PALAVRAS_DE_NARRACAO = 8

#: Narracao de movimento comeca no GERUNDIO - "Olhando pra frente.",
#: "Varrendo a frente.", "Endireitando a cabeca.", "Deixando o radar ligado".
#:
#: Este foi o segundo desenho. O primeiro cortava qualquer frase curta que
#: falasse do assunto da acao, e um teste com fala real derrubou na hora:
#: "Olha so, agora virei parte da historia!" tem sete palavras, tem "olha" e
#: tem "virei" - e e a resposta de verdade. Tamanho nao separa narracao de
#: conteudo; a forma do verbo separa.
_COMECO_DE_NARRACAO = re.compile(r"^\W*\w+ndo\b", re.IGNORECASE)


def _eh_narracao(frase: str, acao: AcaoFalada) -> bool:
    """Esta frase e o robo anunciando o movimento, e nao respondendo?

    Tres condicoes juntas, e as tres precisam valer: curta, comecando em
    gerundio e falando do assunto DAQUELA acao. Qualquer uma sozinha derruba
    conteudo real - foi assim que o primeiro desenho comeu "Olha so, agora
    virei parte da historia!".

    A frase pronta do catalogo passa mesmo fora dessa forma ("Lanterna
    ligada." nao tem gerundio): ela e narracao por definicao, veio daqui.
    """
    if frase.strip().rstrip(".!?").strip() == acao.resposta.rstrip(".!?"):
        return True
    if len(frase.split()) > _PALAVRAS_DE_NARRACAO:
        return False
    if not _COMECO_DE_NARRACAO.match(frase):
        return False
    return _combina_com_acao(acao.nome, frase)


def _sem_narracao(falado: str, acao: AcaoFalada) -> str:
    """Tira do comeco da fala o anuncio do movimento que ninguem pediu.

    So as frases DO COMECO, e so enquanto elas forem curtas e falarem do
    assunto da acao. O modelo copia o exemplo do prompt e abre a resposta com
    "Olhando pra frente." - no meio de uma conversa sobre outra coisa isso e
    ruido, e foi a queixa do dono em 09/08. As vezes ele encadeia duas
    ("Varrendo a frente. Deixando o radar ligado aqui na frente."), por isso
    laco e nao um corte so.

    Na primeira frase que NAO e narracao, para. O que vem dali para a frente e
    a resposta de verdade, e cortar conteudo para calar um tique seria trocar
    um defeito por um pior.
    """
    texto = falado.strip()
    while texto:
        corte = re.search(r"[.!?]+(?:\s+|$)", texto)
        # Sem pontuacao a fala inteira e uma frase so: se for narracao, some.
        primeira = texto[: corte.end()].strip() if corte else texto
        resto = texto[corte.end():].strip() if corte else ""
        if not _eh_narracao(primeira, acao):
            break
        logger.info("narracao nao pedida descartada: %r (acao %s)", primeira, acao.nome)
        texto = resto
    return texto


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

#: PAUSAR e RETOMAR a aula (2026-08-07). Por expressao regular e nao pelo
#: modelo, pelo mesmo motivo de "parar": e ordem, e ordem nao pode depender
#: de cota, de internet nem de o modelo lembrar de escrever um marcador.
#:
#: "pausa" e "espera" cobrem o jeito natural de pedir; "continua" e "segue"
#: cobrem a volta. Palavra inteira sempre - "pausa" casaria dentro de
#: "pausada" e "segue" dentro de "seguem".
_PADRAO_PAUSAR = re.compile(
    r"\bpausa\b|\bpausar\b|\bpause\b|\bespera\b|\bespere\b|\bum momento\b|\bpera\b"
)
_PADRAO_RETOMAR = re.compile(
    r"\bcontinua\b|\bcontinuar\b|\bcontinue\b|\bsegue\b|\bsiga\b"
    r"|\bpode seguir\b|\bpode continuar\b|\bretoma\b|\bvolta a aula\b"
)

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

#: "Ativar modo aula" (2026-08-09): o dono quer o cardapio ANTES de
#: escolher - "ele entra com o sistema de cursos e aulas disponiveis e
#: pergunta qual aula ou curso voce gostaria de comecar". Palavra inteira,
#: sempre, pela licao de "parar" dentro de "preparar".
_PADRAO_MODO_AULA = re.compile(
    r"\bmodo (?:de )?aula\b|\bmodo ensino\b|\bmodo professor\b"
    r"|\bquero estudar\b|\bvamos estudar\b"
)

#: Desistencia na janela de escolha da aula. Apertada de proposito: ela so
#: vale por UM turno depois do cardapio, mas mesmo ali "deixa eu ver" nao
#: pode cancelar nada.
_PADRAO_DESISTIR_AULA = re.compile(
    r"\bcancela\b|\bdeixa pra la\b|\bdeixa para la\b|\bdeixa quieto\b"
    r"|\besquece\b|\bnenhuma\b"
)

#: Teto de tempo da escuta da escolha de aula (2026-08-09). "Um turno" so
#: conta quando a fala chega ao planner - e a transcricao vazia NAO chega
#: (`processar_comando` retorna antes). Medido ao vivo no mesmo dia: o STT
#: esvaziou todas as respostas ao cardapio e, dez minutos depois, o wake
#: word "Fofao! Fofao! Fofao!" foi consumido como escolha de aula. Sem teto
#: de tempo, a escuta dura a primeira fala nao-vazia, quando quer que ela
#: venha - o modo grudento que "um turno" existia para impedir.
_ESCOLHA_AULA_TIMEOUT_S = 120.0

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

#: "o que é isso?" / "o que é o objeto na minha mão?" / "que objeto é esse?"
#:
#: A pergunta sobre o mundo ATUAL - o que a camera ve AGORA, e nao o que o
#: diario viu ha meia hora. Foi o fio quente apontado em 2026-08-09: o robo
#: respondia do passado ou a IA chutava sem ver o frame. O atalho existe
#: pela mesma razao do relogio: a visao consulta e um sensor, nao precisa de
#: modelo - e o modelo, sem o quadro, inventa.
#:
#: COBERTURA: "o que e isso/isto/esse/este?", "o que e esse objeto", "o que
#: e o objeto na minha mao/ai?", "que objeto e esse?", "o que esta na
#: frente?", "o que voce esta vendo?", "o que voce esta segurando?", "o que
#: tem na (sua/minha) mao" e variacoes transcritas pelo Whisper em producao
#: em 2026-08-09 ("o que e que voce esta segurando a minha mao?").
#:
#: SEGURANCA CONTRA SEQUESTRO (a licao de "parar" dentro de "preparar"):
#: apos o sujeito, so entram palavras fechadas de LUGAR (`_LOCAL_DA_CENA`) -
#: "aí", "na minha mao", "na frente". Qualquer outra palavra depois ("o que
#: e isso QUE voce disse?", "o que e esse discurso?") e outra pergunta, e a
#: cascata segue para a IA. O `$` no fim garante isso.
_LOCAL_DA_CENA = (
    r"(?:(?:a[ií])"
    r"|(?:em\s+|na\s+)?(?:minha\s+|sua\s+)?(?:m[ãa]o|frente)"
    r"|à\s+frente)"
)
_PADRAO_VER_AGORA = re.compile(
    r"\bo\s+que\s+[ée]\s+"
    r"(?:isso|isto|esse|este|essa|esta|essa\s+coisa|esse\s+objeto|este\s+objeto)\b"
    + r"(?:\s+(?:que\s+est[áa]\s+)?" + _LOCAL_DA_CENA + r")?\s*[?.!]*$"
    r"|\bo\s+que\s+[ée]\s+o\s+objeto\b"
    + r"(?:\s+(?:que\s+est[áa]\s+)?" + _LOCAL_DA_CENA + r")?\s*[?.!]*$"
    r"|\bque\s+[ée]\s+(?:isso|isto|esse|este|essa\s+coisa)\b"
    + r"(?:\s+(?:que\s+est[áa]\s+)?" + _LOCAL_DA_CENA + r")?\s*[?.!]*$"
    r"|\bque\s+(?:objeto|coisa)\s+[ée]\s+(?:esse|este|isso|isto|essa)\b"
    + r"(?:\s+(?:que\s+est[áa]\s+)?" + _LOCAL_DA_CENA + r")?\s*[?.!]*$"
    r"|\bo\s+que\s+(?:ta|tá|esta|está|tem)\s+(?:ai\s+|aí\s+)?(?:na\s+)?(?:minha\s+)?frente\s*[?.!]*$"
    r"|\bo\s+que\s+(?:[ée]\s+que\s+)?(?:voce|você|cê)\s+(?:est[áa]|t[áa])?\s*"
    + r"segur(?:ando|a)\b(?:\s+(?:na|a|em)?\s*(?:minha|sua)?\s*m[ãa]o?)?\s*[?.!]*$"
    r"|\bo\s+que\s+(?:tem|est[áa]|t[áa])\s+(?:ai\s+|aí\s+)?(?:na\s+|em\s+)?(?:minha\s+|sua\s+)?m[ãa]o?[?.!]*$"
    r"|\bo\s+que\s+(?:voce|você|cê)\s+(?:esta|está|ta|tá)\s+vendo\s*[?.!]*$"
    r"|(?:voce|você|cê)\s+(?:esta|está|ta|tá)\s+vendo\s+o\s+que\s*[?.!]*$"
)

#: Nome em portugues para as classes do YOLO (COCO) que fazem sentido numa
#: casa. Fora daqui, o nome do modelo vem como ele da. Pequeno de proposito:
#: traducao de catalogo e trabalho do sistema, nao do modelo - o robo fala a
#: classe que E, em portugues, para quem pergunta.
_NOME_PT_DAS_CLASSES = {
    "dog": "cachorro",
    "cat": "gato",
    "bird": "passaro",
    "cell phone": "celular",
    "chair": "cadeira",
    "couch": "sofa",
    "tv": "televisao",
    "book": "livro",
    "remote": "controle remoto",
    "bottle": "garrafa",
    "cup": "copo",
    "keyboard": "teclado",
    "laptop": "notebook",
    "table": "mesa",
    "potted plant": "vaso de planta",
    "backpack": "mochila",
    "clock": "relogio",
    "refrigerator": "geladeira",
}


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
        jarvis_plugins=None,  # Registry de plugins JARVIS (opcional)
        enviar_comando_hardware: CallbackComandoHardware | None = None,
        memory_client: MemoryClient | None = None,
        diario=None,
        familia: list[dict] | None = None,
        mover_cabeca: CallbackCabeca | None = None,
        deixar_recado: CallbackRecado | None = None,
        guardar_conhecimento: CallbackConhecimento | None = None,
        consultar_internet=None,
        consultar_visao: CallbackVisao | None = None,
        professor=None,
        autoconhecimento=None,
        ementas=None,
        estado_corpo: CallbackEstadoCorpo | None = None,
    ) -> None:
        #: O corpo agora (LiDAR, bussola, ultimo movimento conferido) - entra
        #: no prompt junto do catalogo de acoes. Sem isto o modelo decidia
        #: "anda para frente" sem saber se havia uma parede a 20 cm.
        self._estado_corpo = estado_corpo
        #: Responde "o que voce ensina?" a partir do mapa das ementas.
        self._ementas = ementas
        # A escuta da escolha de aula dura UM turno (modo aula,
        # 2026-08-09). Modo grudento que reinterpreta conversa fora
        # dele e o sequestro que este projeto persegue.
        self._aguardando_escolha_aula = False
        #: ...e tem teto de tempo (`_ESCOLHA_AULA_TIMEOUT_S`): turno vazio
        #: nao chega aqui, entao "um turno" sozinho nao expira nunca.
        self._escolha_aula_expira_em = 0.0
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
        #: Quem diz o que a camera ve AGORA (2026-08-09). None = o robo nao
        #: responde "o que e isso?" por aqui e a pergunta vai para a IA -
        #: que nao ve o frame e inventa. Mesmo contrato dos outros callbacks:
        #: sem caminho, ele nao finge.
        self._consultar_visao = consultar_visao
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
        #: Registry de plugins JARVIS para function calling (code_exec, file_ops, etc)
        self._jarvis_plugins = jarvis_plugins or get_registry()

    async def processar(self, texto_usuario: str, pessoa_id: int | None = None) -> str:
        contexto = await self._consultar_contexto(pessoa_id, texto_usuario)

        if self._pediu_para_parar(texto_usuario):
            # "para" tambem encerra a aula. Sem isto, o robo pararia as rodas
            # e continuaria dando aula - obedecendo pela metade, que e pior
            # que nao obedecer, porque parece que ele ignorou.
            if self._professor is not None:
                await self._professor.parar()
            resposta = await self._executar_comando("STOP", RESPOSTA_PARAR)
        elif (aula_pausada := self._pausar_ou_retomar(texto_usuario)) is not None:
            resposta = aula_pausada
        elif (escolha := await self._escolher_aula_pendente(texto_usuario)) is not None:
            resposta = escolha
        elif (modo_aula := self._ativar_modo_aula(texto_usuario)) is not None:
            resposta = modo_aula
        elif (aula := await self._tentar_dar_aula(texto_usuario)) is not None:
            resposta = aula
        elif (programa := self._responder_ementa(texto_usuario)) is not None:
            resposta = programa
        elif (sobre_mim := self._responder_sobre_mim(texto_usuario)) is not None:
            resposta = sobre_mim
        elif (ja_sei := await self._responder_do_saber(texto_usuario)) is not None:
            resposta = ja_sei
        elif (jarvis_resp := await self._responder_plugins_jarvis(texto_usuario)) is not None:
            resposta = jarvis_resp
        elif self._eh_pergunta_de_hora(texto_usuario):
            resposta = self._responder_hora()
        elif (resposta_diario := await self._responder_sobre_quem_viu(
            texto_usuario, contexto
        )) is not None:
            resposta = resposta_diario
        elif (visao_agora := await self._responder_sobre_o_que_ve(
            texto_usuario
        )) is not None:
            resposta = visao_agora
        else:
            await self._contar_consulta_ia()
            bruta = await self._ai_manager.responder(texto_usuario, contexto)
            # Mesmo degrau do caminho por frases: se a IA nao respondeu, ele
            # tenta obedecer sozinho antes de dizer que nao consegue pensar.
            if bruta == SEM_IA and (local := await self._obedecer_sem_ia(texto_usuario)):
                resposta = local
            else:
                resposta = await self._cumprir_acao_pedida(bruta, texto_usuario)

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
        elif (aula_pausada := self._pausar_ou_retomar(texto_usuario)) is not None:
            resposta = aula_pausada
        elif (escolha := await self._escolher_aula_pendente(texto_usuario)) is not None:
            resposta = escolha
        elif (modo_aula := self._ativar_modo_aula(texto_usuario)) is not None:
            resposta = modo_aula
        elif (aula := await self._tentar_dar_aula(texto_usuario)) is not None:
            resposta = aula
        elif (programa := self._responder_ementa(texto_usuario)) is not None:
            resposta = programa
        elif (sobre_mim := self._responder_sobre_mim(texto_usuario)) is not None:
            resposta = sobre_mim
        elif (ja_sei := await self._responder_do_saber(texto_usuario)) is not None:
            resposta = ja_sei
        elif (jarvis_resp := await self._responder_plugins_jarvis(texto_usuario)) is not None:
            resposta = jarvis_resp
        elif self._eh_pergunta_de_hora(texto_usuario):
            resposta = self._responder_hora()
        elif (resposta_diario := await self._responder_sobre_quem_viu(
            texto_usuario, contexto
        )) is not None:
            resposta = resposta_diario
        elif (visao_agora := await self._responder_sobre_o_que_ve(
            texto_usuario
        )) is not None:
            resposta = visao_agora
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
        await self._contar_consulta_ia()
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
                # `pode_calar`: aqui sobrar nada e aceitavel, porque as frases
                # seguintes ainda vem. Era exatamente nesta primeira frase que
                # a narracao nao pedida saia no alto-falante.
                frase = await self._cumprir_acao_pedida(
                    frase, texto_usuario, pode_calar=True
                )
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
        # O CORPO AGORA vai colado ao catalogo, e nao num bloco proprio: e
        # onde o modelo decide se vai agir, e o mapa e o que faz essa decisao
        # ter fundamento. Falhar aqui so tira a informacao - nunca a conversa.
        if self._estado_corpo is not None:
            try:
                corpo = await self._estado_corpo()
            except Exception:
                logger.exception("falha ao ler o estado do corpo")
                corpo = None
            if corpo:
                contexto["acoes"] += "\n\n" + corpo

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

        # A VISAO ATUAL entra quando a cena e fresca (<3 s): o AGORA na
        # frente da camera, diferente do diario (o dia inteiro). Sem este
        # bloco, uma pergunta sobre o instante que nao casar com o padrao
        # deterministico cai na IA e ela responde do diario ou inventa - o
        # impasse medido em producao em 2026-08-09, quando o Whisper
        # transcreveu "o que e que voce esta segurando a minha mao?" fora do
        # padrao e a IA respondeu "e o celular ou o controle?". O snapshot
        # no prompt faz a resposta sair dos fatos da camera.
        #
        # `ver_agora()` devolve None para cena velha: abertura sem
        # afirmacao - sem o bloco, a IA nao fala do passado como se fosse
        # agora. Quando a camera VE algo (mesmo "nada"), a frase descreve a
        # cena traduzida, com o mesmo texto que o caminho deterministico.
        if self._consultar_visao is not None:
            try:
                cena = self._consultar_visao()
                if cena is not None:
                    contexto["visao_agora"] = self._descrever_cena(cena)
            except Exception:
                logger.debug("Falha ao consultar a visao atual para o contexto", exc_info=True)

        return contexto or None

    def _pausar_ou_retomar(self, texto: str) -> str | None:
        """Segura e solta a aula. None = a frase nao era sobre isso.

        SO responde quando ha aula em andamento: sem essa guarda, "espera ai"
        no meio de uma conversa qualquer viraria uma resposta sobre aula que
        ninguem esta tendo.

        Existe porque o robo NAO ESCUTA enquanto fala (ver `sintese.py`), e
        numa aula de doze minutos ele ficaria surdo o tempo todo. A janela em
        que ele ouve e a pausa entre os passos - e por isso ela subiu para
        1,5 s no professor.
        """
        if self._professor is None or not self._professor.dando_aula:
            return None
        if _PADRAO_RETOMAR.search(texto.lower()):
            return "Continuando a aula." if self._professor.retomar() else None
        if _PADRAO_PAUSAR.search(texto.lower()):
            if self._professor.pausar():
                return "Pausei a aula. Fala continua quando quiser seguir."
            return None
        return None

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

    async def _responder_plugins_jarvis(self, texto: str) -> str | None:
        """Executa plugins JARVIS via function calling do Gemini.

        Os plugins disponiveis: code_executor, file_operations, system_control,
        screen_awareness, ros2_navigator, desktop_control.

        Retorna a resposta do plugin se executado, None se nenhum plugin aplicavel.
        """
        if self._jarvis_plugins is None:
            return None
        try:
            schemas = self._jarvis_plugins.get_function_schemas()
            if not schemas:
                return None
            # Usar o AiManager com function calling para executar o plugin
            # O AiManager.responder aceita parâmetro tools
            tools = [{"function_declarations": schemas}]
            contexto = await self._consultar_contexto(None, texto)
            resposta = await self._ai_manager.responder(texto, contexto, tools=tools)
            
            # Se a resposta contém function call, executar
            # O AiManager já executa function calls automaticamente quando tools fornecido
            # A resposta final já inclui o resultado da execução
            return resposta
        except Exception as e:
            logger.exception("Erro ao executar plugins JARVIS")
            return None

    async def _responder_do_saber(self, texto: str) -> str | None:
        """O "Eu ja sei?" - a memoria cognitiva, antes da IA e nao depois.

        O autoconhecimento cobre o CORPO; este degrau cobre o que ele APRENDEU
        e guardou: conhecimentos, experiencias, procedimentos, dispositivos e
        relacoes (a fundacao da Camada 1, 2026-08-09). Quando a fala COBRE um
        registro no `saber_fts`, a resposta sai daqui, do proprio acervo - a
        IA (a consultora externa) nao e chamada.

        None = ele nao sabe (ou a memoria esta ausente/fora) e a fala segue
        para a IA. Mesmo contrato dos outros degraus locais: sem caminho, ele
        nao finge - e a resposta que sai daqui so contem o que foi guardado.
        """
        if self._memory_client is None:
            return None
        try:
            achados = await self._memory_client.saber_buscar(texto)
        except Exception:
            # Memoria fora nao pode emudecer a conversa: ele perde essa
            # resposta e segue para a IA, como em todos os degraus locais.
            logger.exception("falha ao consultar o conhecimento proprio")
            return None
        if not achados:
            return None
        topo = achados[0]
        titulo = str(topo.get("titulo") or "").strip()
        corpo = str(topo.get("corpo") or "").strip()
        if not titulo:
            return None
        # A resposta e o proprio conhecimento, montado do registro. Nada
        # inventado: ele diz o que sabe, e so o que sabe.
        return f"{titulo}. {corpo}".strip() if corpo else titulo

    async def _contar_consulta_ia(self) -> None:
        """Conta mais uma consulta a IA - a medida de dependencia externa.

        Roda quando a cascata DE FATO caiu no modelo (a consultora externa).
        O contador vive no Pi (`configuracao_memoria`); o planner apenas
        dispara o incremento como tarefa em segundo plano, para o numero
        subir sem esperar rede - atrasar a fala por estatistica nao vale,
        e perder um incremento por corrida seria aceitavel do mesmo jeito.
        """
        if self._memory_client is None:
            return

        async def _contar() -> None:
            try:
                await self._memory_client.registrar_consulta_ia()
            except Exception:
                logger.warning("falha ao contar a consulta a IA", exc_info=True)

        asyncio.create_task(_contar())

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

    def _ativar_modo_aula(self, texto: str) -> str | None:
        """'Ativar modo aula': ele mostra o cardapio e PERGUNTA por onde comecar.

        Pedido do dono (2026-08-09): entrar no sistema de cursos com o
        contexto do que ha de pronto, e a pergunta "qual aula ou curso voce
        gostaria de comecar" - em vez de exigir que quem pergunta saiba o
        nome da aula de cor. A resposta seguinte e tratada como ESCOLHA
        (`_escolher_aula_pendente`): vale dizer so "banco de dados".
        """
        if self._professor is None:
            return None
        if not _PADRAO_MODO_AULA.search(texto.lower()):
            return None
        self._aguardando_escolha_aula = True
        self._escolha_aula_expira_em = time.monotonic() + _ESCOLHA_AULA_TIMEOUT_S
        return self._professor.apresentar_cardapio()

    async def _escolher_aula_pendente(self, texto: str) -> str | None:
        """A resposta ao "qual aula voce queria comecar?" - vale o nome seco.

        A escuta expira SEMPRE neste turno: achou a aula, desistiu ou falou
        de outra coisa, ela acabou e a frase segue a cascata normal. Um modo
        que ficasse escutando para sempre reinterpretaria conversa que nao
        e escolha - o sequestro que este projeto persegue. E expira tambem
        por TEMPO (`_ESCOLHA_AULA_TIMEOUT_S`): turno vazio nao passa por
        aqui, e sem o teto a escuta esperava a primeira fala nao-vazia por
        tempo indeterminado.
        """
        if not self._aguardando_escolha_aula:
            return None
        if time.monotonic() > self._escolha_aula_expira_em:
            self._aguardando_escolha_aula = False
            return None  # a janela fechou: a frase e conversa normal
        if _PADRAO_MODO_AULA.search(texto.lower()):
            return None  # pediu o cardapio de novo; quem responde e _ativar
        self._aguardando_escolha_aula = False
        if _PADRAO_DESISTIR_AULA.search(texto.lower()):
            return "Combinado, sem aula por agora. Se quiser, e so pedir."
        if self._professor is None:
            return None
        aula = self._professor.procurar(texto, nivel=_normalizar_nivel(texto))
        if aula is None:
            # Ele PERGUNTOU qual aula; ouvir um nome que nao tem no banco
            # tem resposta honesta pronta ("nao tenho, sei dar estas"), e
            # nao queda para a IA conversar de outra coisa como se a
            # escolha nao tivesse acontecido.
            return self._professor.oferecer_o_que_tem()
        await self._professor.dar_aula(aula)
        # Mesma frase de abertura do pedido direto: ela diz o NIVEL que ele
        # escolheu, porque pode nao ser o que a pessoa pediu.
        return f"Vou dar uma aula {_ARTIGO_NIVEL[aula.nivel]} sobre {aula.titulo}. Olha o quadro."

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

    async def _cumprir_acao_pedida(
        self, texto: str, texto_usuario: str = "", pode_calar: bool = False
    ) -> str:
        """Faz o que o modelo pediu e devolve so o que deve ser FALADO.

        Sem marcador, devolve o texto intacto - que e o caso da maioria
        esmagadora das frases, porque conversa nao e pedido.

        A FALA TEM PREFERENCIA SOBRE A FRASE PRONTA do catalogo. O modelo
        escreveu "Claro, olhando pra esquerda agora" sabendo com quem esta
        falando e do que estavam falando; a `resposta` do catalogo nao sabe de
        nada disso. Ela so entra quando o modelo mandou a acao e nao escreveu
        nada depois - um robo que age em silencio parece um robo que travou.

        AGIR SEM PEDIDO E PERMITIDO; ANUNCIAR SEM PEDIDO, NAO (dono, 09/08:
        *"ele pode se mexer sem ninguem pedir, ele e autonomo; o q precisa e
        nao ficar falando coisas q nao tem nada a ver"*). Entao a acao roda
        sempre - nada aqui a barra - e o que some e a NARRACAO dela quando a
        frase da pessoa nao pediu nada daquilo. Medido no chat de 08 e 09/08:
        "Voce conhece a Biblia?" recebia "Olhando pra frente.".

        `pode_calar` diz se sobrar nada e aceitavel. No caminho por frases sim,
        porque as frases seguintes ainda vem; na resposta inteira nao, porque
        ali o silencio seria a resposta toda.

        FALHAR AQUI NAO EMUDECE O ROBO: se o servo nao responde, ele diz que
        nao conseguiu. O modo de falha que este projeto mais repete e o
        contrario - a acao nao acontece e ninguem fica sabendo.
        """
        acao, argumento, falado = _extrair_acao(texto)
        if acao is None:
            return falado

        pedida = _pediu_de_fato(acao.nome, texto_usuario)
        logger.info(
            "acao falada: %s %s (pedida=%s)", acao.nome, argumento, pedida
        )
        try:
            if acao.entrega == "hardware":
                await self._despachar_comando(acao, argumento)
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
            elif not _pediu_para_guardar(texto_usuario):
                # NAO grava o que ninguem mandou gravar - ver
                # `pediu_para_guardar`. E diz que nao gravou, porque a frase
                # do modelo aqui costuma ser "guardei" ou "anotei", e deixar
                # isso passar seria a mentira de sempre com o agravante de o
                # acervo ficar limpo enquanto a pessoa acha que ensinou.
                logger.info(
                    "guardar_conhecimento ignorado, ninguem mandou guardar: %r",
                    argumento,
                )
                return "Não anotei isso — se quiser que eu guarde, é só dizer 'guarda isso'."
            else:
                await self._despachar_conhecimento(argumento)
        except _SemCaminho as erro:
            logger.warning("acao %r pedida sem caminho ate o hardware: %s", acao.nome, erro)
            # "Ainda nao consigo fazer isso" e resposta a um PEDIDO. Sem
            # pedido ela e a mesma intromissao que esta funcao existe para
            # tirar - so que agora anunciando uma falha que ninguem esperava.
            if not pedida:
                return _sem_narracao(falado, acao)
            return falado or "Ainda nao consigo fazer isso."
        except _NaoGuardou:
            # NAO devolve `falado`: a frase do modelo diz "combinado, eu
            # aviso" ou "guardei", e essa e exatamente a mentira que este
            # caminho existe para nao repetir. Quem esta ouvindo precisa saber
            # que nao pode contar com isso.
            return "Desculpe, nao consegui guardar isso."
        except Exception as erro:
            # A TRAVA DO LIDAR DIZ POR QUE (2026-11-01). O Pi recusa comando
            # de roda com obstaculo no caminho e manda o motivo no NACK
            # ("obstaculo a 24 cm para frente"). Ate aqui ele virava
            # "Nao consegui" e o dono achava que o robo estava quebrado.
            motivo = str(erro)
            if "obstaculo a " in motivo:
                trecho = motivo.split("obstaculo a ", 1)[1].split(" (limite", 1)[0]
                logger.info("acao %r recusada pela trava do LiDAR: %s", acao.nome, trecho)
                return f"Nao vou andar: tem um obstaculo a {trecho}."
            if "nao ando as cegas" in motivo:
                return "Nao vou andar: estou sem leitura do LiDAR agora."
            logger.exception("falha ao executar a acao %r", acao.nome)
            return "Nao consegui fazer isso agora."

        if pedida:
            return falado or acao.resposta
        limpo = _sem_narracao(falado, acao)
        # Sobrou nada e nao da para calar: a frase pronta volta. Um robo mudo
        # depois de ouvir seu nome e pior que um robo que narra demais - e
        # este e o unico caminho em que o silencio seria a resposta inteira.
        return limpo if (limpo or pode_calar) else acao.resposta

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

    async def _despachar_comando(self, acao: AcaoFalada, argumento: str = "") -> None:
        if self._enviar_comando_hardware is None:
            raise _SemCaminho("sem callback de comando de hardware")
        parametros = _parametros_do_comando(acao, argumento)
        if parametros:
            await self._enviar_comando_hardware(acao.comando, parametros)
        else:
            # So o nome, como sempre foi: quem implementa o callback com um
            # parametro so (luz, ventilador, testes antigos) continua valendo.
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

    def registrar_consultar_visao(self, callback: CallbackVisao | None) -> None:
        """Liga a visao ao planner DEPOIS de ela subir (2026-08-09).

        No `conversar_jarvis.py` o MissionPlanner e construido ANTES do
        ModuloVisao (a camera carrega o YOLO no `iniciar()`), entao este fio
        nao pode nascer no `__init__`. Quem monta o boot chama este metodo
        assim que o modulo de visao esta no ar.

        None desliga de novo: se a visao cair a meio do dia, o robo volta a
        deixar a pergunta "o que e isso?" para a IA - e volta a inventar,
        que e honesto demais para nao ser, mas e o que o contrato manda
        (sem caminho, ele nao finge).
        """
        self._consultar_visao = callback

    async def _responder_sobre_o_que_ve(self, texto: str) -> str | None:
        """Responde "o que e isso?" com o que a camera ve AGORA.

        MESMA RAZAO DO DIARIO (NAO VAI PARA A IA): e leitura de sensor, no
        instante. O modelo nao ve o frame - e, perguntado, inventa: foi o
        impasse que abriu esta pendencia ("nao identifica objetos"). A
        resposta vem de quem tem o dado do momento (o Vision Core), nao de
        quem apalpa no escuro.

        None = nao ha de onde tirar a resposta (callback ausente) ou a frase
        nao e essa pergunta - a cascata segue para os proximos degraus.
        """
        if self._consultar_visao is None:
            return None
        if not _PADRAO_VER_AGORA.search(texto.lower()):
            return None
        try:
            cena = self._consultar_visao()
        except Exception:
            logger.exception("falha ao consultar a visao atual")
            return "Nao estou vendo nada na minha frente agora."
        return self._descrever_cena(cena)

    @staticmethod
    def _descrever_cena(cena: dict | None) -> str:
        """Transforma o snapshot da visao na fala: "estou vendo X, Y e o Z".

        Resposta direta, dos fatos da cena - nada inventado. O robo diz o
        que a camera diz, traduzido so no rotulo (pesquisa em catalogo, nao
        achismo do modelo).
        """
        if not isinstance(cena, dict):
            return "Nao estou vendo nada na minha frente agora."
        itens: list[str] = []
        vistos: set[str] = set()
        for objeto in cena.get("objetos") or []:
            if not isinstance(objeto, dict):
                continue
            classe = str(objeto.get("classe") or "").strip()
            if not classe or classe == "person":
                continue
            rotulo = _NOME_PT_DAS_CLASSES.get(classe, classe)
            if rotulo in vistos:
                continue
            vistos.add(rotulo)
            itens.append(rotulo)
        for reconhecido in cena.get("reconhecidos") or []:
            if not isinstance(reconhecido, dict):
                continue
            nome = str(reconhecido.get("nome") or "").strip()
            if nome:
                itens.append(f"o {nome}")
        desconhecidos = int(cena.get("desconhecidos") or 0)
        if desconhecidos > 0:
            itens.append("alguem que eu nao conheco")
        # GENTE SEM ROSTO TAMBEM E GENTE (2026-11-01). A classe `person` e
        # pulada acima porque quem tem rosto achado ja entra pelo nome ou como
        # desconhecido. Mas de costas, longe ou de lado o rosto nao e achado,
        # e ate aqui a resposta era "nao estou vendo nada" com o YOLO vendo
        # alguem. Conta as pessoas que sobram depois dos rostos.
        pessoas = int(cena.get("pessoas") or 0)
        if not pessoas and any(
            isinstance(o, dict) and o.get("classe") == "person"
            for o in cena.get("objetos") or []
        ):
            pessoas = 1
        sem_rosto = pessoas - len(cena.get("reconhecidos") or []) - desconhecidos
        if sem_rosto == 1:
            itens.append("uma pessoa")
        elif sem_rosto > 1:
            itens.append(f"{sem_rosto} pessoas")
        if not itens:
            return "Nao estou vendo nada na minha frente agora."
        if len(itens) == 1:
            return f"Na minha frente agora estou vendo {itens[0]}."
        return "Na minha frente agora estou vendo {}, e {}.".format(
            ", ".join(itens[:-1]), itens[-1]
        )

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
