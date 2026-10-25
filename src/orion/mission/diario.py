"""Diário do robô: o que o Jarvis viu, gravado e recuperável (camada 2).

O QUE FALTAVA
-------------
A camada 2 do grounding (`orion/mission/grounding.py`) já sabia formatar
observações e, mais importante, sabia dizer "não tenho NENHUM registro
hoje". Só que nada nunca preenchia essa lista - ela era sempre vazia. O
robô era honesto por falta de memória, não por ter olhado.

Este módulo é quem escreve o diário e quem o lê de volta.

O QUE ENTRA NO DIÁRIO (e o que não entra)
------------------------------------------
Entra o que uma pessoa perguntaria depois: quem apareceu, quando, e se foi
alguém reconhecido. Não entra telemetria, estado de motor nem nada de alta
frequência - isso já vive na tabela `telemetria` e encheria o contexto da
IA de ruído, empurrando para fora justamente o que importa.

O CUIDADO QUE MAIS IMPORTA: NÃO INUNDAR
----------------------------------------
`vision.person_detected` dispara a cada verificação enquanto a pessoa
estiver na frente da câmera. Gravar tudo produziria centenas de linhas
"vi o João Paulo" por hora - o banco cresceria à toa e o bloco de contexto
viraria uma parede de repetição, que é a melhor forma de fazer um modelo
pequeno ignorar o que interessa. Por isso há uma janela de silêncio por
pessoa: revê-la dentro dela não vira registro novo.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("orion.mission.diario")

CATEGORIA = "eventos"
ORIGEM = "diario"

#: Tipos gravados no diário. Nomes curtos porque vão para dentro do prompt.
TIPO_PESSOA_VISTA = "pessoa_vista"
TIPO_DESCONHECIDO = "desconhecido_visto"
#: DIÁRIO AMPLO (2026-07-29, pedido do dono: *"um diário capaz de cobrir tudo
#: o que possamos solicitar"*, junto com *"tem que ensinar a usar a câmera para
#: ver o ambiente"*).
#:
#: `vision.object_detected` era publicado a cada quadro pelo Vision Core e
#: NINGUÉM assinava - a câmera já classificava cadeira, copo, celular, e essa
#: informação evaporava. Sem isto o robô convive com a casa todos os dias e não
#: sabe dizer uma única coisa que existe nela.
TIPO_OBJETO_VISTO = "objeto_visto"
#: O que foi PEDIDO a ele. Sem isto ele não consegue responder "o que eu te
#: pedi hoje?", nem perceber que já lhe pediram a mesma coisa três vezes.
TIPO_PEDIDO_OUVIDO = "pedido_ouvido"

#: Janela de silêncio por pessoa. Ver o João Paulo de novo 2 minutos depois
#: não é fato novo - é a mesma presença continuando.
JANELA_SILENCIO_S = 600.0

#: Janela por CLASSE de objeto, e ela é longa de propósito.
#:
#: `vision.object_detected` dispara por objeto E por quadro: a 10 quadros por
#: segundo com cinco coisas no campo de visão são 50 eventos por segundo. Sem
#: uma janela larga o diário viraria um despejo de "vi uma cadeira" e empurraria
#: tudo o que importa para fora da janela do prompt.
#:
#: Meia hora porque móvel é MÓVEL: a cadeira que ele viu às 14h continua sendo
#: a mesma cadeira às 14h05. O fato novo é ver uma classe que ele ainda não
#: tinha visto - e isso a janela por classe captura.
JANELA_OBJETO_S = 1800.0

#: Confiança mínima para um objeto valer registro. Abaixo disto o YOLO chuta, e
#: um diário com chute é pior que um diário curto: a IA passa a afirmar que a
#: casa tem coisas que nunca existiram.
CONFIANCA_MINIMA_OBJETO = 0.60

#: Teto de registros lidos para montar o contexto. O bloco vai inteiro para
#: dentro do prompt de um modelo de 1B com janela pequena: melhor poucos
#: registros recentes e legíveis do que um despejo que empurra o resto para
#: fora da janela.
MAX_OBSERVACOES_CONTEXTO = 8


class DiarioObservacoes:
    """Escreve no diário a partir dos eventos, e lê de volta para a IA."""

    def __init__(
        self,
        event_bus: EventBus,
        memory_client,
        janela_silencio_s: float = JANELA_SILENCIO_S,
        agora=None,
        apenas_leitura: bool = False,
        janela_objeto_s: float = JANELA_OBJETO_S,
    ) -> None:
        self._bus = event_bus
        self._memoria = memory_client
        self._janela_silencio_s = janela_silencio_s
        self._janela_objeto_s = janela_objeto_s
        # injetável para os testes não dependerem do relógio real
        self._agora = agora or datetime.now
        #: última vez que cada pessoa virou registro (nome -> datetime)
        self._ultimo_registro: dict[str, datetime] = {}

        #: `apenas_leitura` existe para o Pi (2026-07-29). Quem ESCREVE o
        #: diário é o Notebook, que é onde a visão reconhece as pessoas. O Pi
        #: precisa apenas LER, para pôr as observações no contexto da IA
        #: estratégica - e se ele assinasse os mesmos eventos, gravaria cada
        #: observação DUAS vezes, porque `vision.person_recognized` é
        #: encaminhado do Notebook para cá.
        if apenas_leitura:
            return

        # `vision.person_recognized`, NAO `vision.person_detected` (corrigido
        # em 2026-07-29).
        #
        # O BUG: `person_detected` e publicado para QUALQUER pessoa que o YOLO
        # ve, e o payload real dele e `{classe, confianca, caixa, timestamp}` -
        # nao tem `nome`. O nome so existe em `person_recognized`, que o Vision
        # Core so publica quando o rosto casa com alguem cadastrado
        # (vision_core.py: `if alvo.pessoa_id is not None`).
        #
        # Resultado: `nome` era sempre None, o `if not nome: return` abaixo
        # disparava toda vez, e **nenhuma pessoa reconhecida jamais entrou no
        # diario em producao**. O robo via o dono todo dia e o diario ficava
        # vazio - e o grounding, honestamente, reportava "nao tenho registro".
        #
        # Por que nenhum teste pegou: eles publicavam um evento sintetico com
        # `nome` dentro, um payload que o produtor real nunca emitiu. Dube e
        # produtor com contratos diferentes - a mesma armadilha que deixou o
        # FOLLOW morto em 2026-07-28.
        event_bus.subscribe("vision.person_recognized", self._ao_ver_pessoa)
        event_bus.subscribe("sentinela.alerta", self._ao_receber_alerta)
        # Os DOIS abaixo rodam no Notebook, e o diário também - conferido antes
        # de assinar. Assinar tópico que nunca chega é o defeito que mais se
        # repete neste projeto, e ele não denuncia: `subscribe` não falha.
        #
        #   vision.object_detected  <- src/orion/vision/vision_core.py:200
        #   voice.command_received  <- src/orion/voice/voice_core.py:165
        #
        # NÃO estão aqui, de propósito: `navigation.segment_completed` e
        # `navigation.obstacle_avoided` ("andei 30 cm", "desviei de algo"), que
        # seriam ótimos no diário mas são publicados no PI. Assiná-los aqui
        # criaria exatamente o órfão descrito acima. Entram quando alguém
        # encaminhar os dois pelo link - anotado, não fingido.
        event_bus.subscribe("vision.object_detected", self._ao_ver_objeto)
        event_bus.subscribe("voice.command_received", self._ao_ouvir_pedido)

    # ----- escrita -----

    async def _ao_ver_pessoa(self, evento: Evento) -> None:
        nome = evento.dados.get("nome")
        # Rosto sem nome é tratado pelo alerta da sentinela, não aqui - se
        # gravasse nos dois lugares, o mesmo estranho apareceria duas vezes
        # no contexto e a IA poderia contar como duas visitas.
        if not nome:
            return
        if not self._passou_a_janela(nome):
            return
        await self._gravar(TIPO_PESSOA_VISTA, {"nome": nome})

    async def _ao_receber_alerta(self, evento: Evento) -> None:
        if evento.dados.get("tipo") != "pessoa":
            return  # alerta de barulho etc. não é "alguém apareceu"
        if not self._passou_a_janela("__desconhecido__"):
            return
        await self._gravar(TIPO_DESCONHECIDO, {})

    async def _ao_ver_objeto(self, evento: Evento) -> None:
        """A câmera vendo o ambiente - móveis, objetos, o que existe na casa."""
        classe = evento.dados.get("classe")
        if not classe:
            return
        confianca = float(evento.dados.get("confianca") or 0.0)
        if confianca < CONFIANCA_MINIMA_OBJETO:
            return
        # Janela POR CLASSE: ver a mesma cadeira de novo não é fato novo; ver
        # uma classe inédita é.
        if not self._passou_a_janela(f"objeto:{classe}", self._janela_objeto_s):
            return
        await self._gravar(TIPO_OBJETO_VISTO, {"classe": classe})

    async def _ao_ouvir_pedido(self, evento: Evento) -> None:
        """O que lhe pediram. Guardar o PEDIDO, não a resposta: é o pedido que
        ele precisa lembrar para saber o que já lhe foi cobrado hoje."""
        texto = (evento.dados.get("texto") or "").strip()
        if not texto:
            return  # "não entendi" não é pedido, e o portão do Whisper produz isso
        # Dedup pelo próprio texto: repetir o comando porque ele não obedeceu
        # da primeira vez não são dois pedidos diferentes.
        if not self._passou_a_janela(f"pedido:{texto.lower()[:40]}"):
            return
        await self._gravar(TIPO_PEDIDO_OUVIDO, {"texto": texto[:120]})

    def _passou_a_janela(self, chave: str, janela_s: float | None = None) -> bool:
        agora = self._agora()
        limite = self._janela_silencio_s if janela_s is None else janela_s
        anterior = self._ultimo_registro.get(chave)
        if anterior is not None:
            if (agora - anterior).total_seconds() < limite:
                return False
        self._ultimo_registro[chave] = agora
        return True

    async def _gravar(self, tipo: str, dados: dict[str, Any]) -> None:
        try:
            await self._memoria.remember(
                CATEGORIA,
                {
                    "origem": ORIGEM,
                    "tipo": tipo,
                    "payload_json": json.dumps(dados, ensure_ascii=False),
                },
            )
            logger.info("diário: %s %s", tipo, dados or "")
        except Exception:
            # Memória fora do ar não pode derrubar a visão nem a conversa -
            # o robô fica sem diário, o que o grounding já sabe reportar
            # ("não tenho registro"), em vez de mentir.
            logger.warning("falha ao gravar no diário (%s)", tipo, exc_info=True)

    # ----- leitura -----

    async def observacoes_de_hoje(self) -> list[dict[str, str]]:
        """Registros de hoje, no formato que o grounding espera.

        Devolve lista vazia quando não há nada - e isso É informação: o
        grounding a transforma em "não tenho NENHUM registro hoje", que é
        o que impede a IA de inventar uma visita.
        """
        try:
            registros = await self._memoria.recall(
                CATEGORIA, filtro={"origem": ORIGEM}, limite=MAX_OBSERVACOES_CONTEXTO * 3
            )
        except Exception:
            logger.warning("falha ao ler o diário", exc_info=True)
            return []

        inicio_do_dia = self._agora().replace(hour=0, minute=0, second=0, microsecond=0)
        observacoes: list[dict[str, str]] = []

        for registro in registros:
            quando = _comparavel_com(
                _parse_timestamp(registro.get("timestamp")), inicio_do_dia
            )
            if quando is None or quando < inicio_do_dia:
                continue  # ontem não conta como "hoje"
            observacoes.append(
                {"quando": quando.strftime("%H:%M"), "o_que": _descrever(registro)}
            )
            if len(observacoes) >= MAX_OBSERVACOES_CONTEXTO:
                break

        # recall devolve o mais recente primeiro; no texto a ordem
        # cronológica é mais natural de ler (e de a IA narrar).
        observacoes.reverse()
        return observacoes


def _comparavel_com(quando: datetime | None, referencia: datetime) -> datetime | None:
    """Deixa `quando` na mesma natureza de fuso que `referencia`.

    BUG REAL (2026-07-29), e ele só apareceu quando o diário voltou a gravar:
    o banco guarda o horário COM fuso (`...+00:00`) e `self._agora()` é um
    `datetime.now()` SEM fuso. Comparar os dois levanta

        TypeError: can't compare offset-naive and offset-aware datetimes

    e derrubava `_consultar_contexto` inteiro no MissionPlanner - ou seja, a
    conversa perdia o bloco de observações justamente no dia em que ele deixou
    de vir vazio. Ficou escondido desde sempre porque a lista chegava sem
    registros e o laço nunca executava esta comparação.

    Converte para a hora LOCAL antes de tirar o fuso. Descartar o `tzinfo`
    direto trataria 10:45 UTC como 10:45 local e jogaria as observações da
    madrugada para o dia errado - um erro silencioso, que é o pior tipo.
    """
    if quando is None:
        return None
    if quando.tzinfo is not None and referencia.tzinfo is None:
        return quando.astimezone().replace(tzinfo=None)
    if quando.tzinfo is None and referencia.tzinfo is not None:
        return quando.replace(tzinfo=referencia.tzinfo)
    return quando


def _parse_timestamp(valor: Any) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return None


def _descrever(registro: dict[str, Any]) -> str:
    """Vira frase curta em português - o texto entra direto no prompt."""
    tipo = registro.get("tipo")
    try:
        dados = json.loads(registro.get("payload_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        dados = {}

    if tipo == TIPO_PESSOA_VISTA:
        return f"vi {dados.get('nome', 'alguém')}"
    if tipo == TIPO_DESCONHECIDO:
        return "vi uma pessoa que não reconheci"
    if tipo == TIPO_OBJETO_VISTO:
        return f"vi um objeto da classe '{dados.get('classe', '?')}' pela câmera"
    if tipo == TIPO_PEDIDO_OUVIDO:
        return f"me pediram: \"{dados.get('texto', '?')}\""
    return str(tipo)


def hoje_ate(agora: datetime, horas: int) -> datetime:
    """Auxiliar de teste/consulta: instante de N horas atrás."""
    return agora - timedelta(hours=horas)
