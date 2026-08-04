"""Interface Web (Cap 13 s.4-5, 7) — servida pelo Raspberry Pi.

Diferente do avatar (Notebook, Cap 13 s.3), que e um repassador puro sem
estado, o Dashboard precisa mostrar o estado ATUAL do sistema pra quem
acabou de abrir a pagina - por isso este modulo mantem um pequeno cache em
memoria do ultimo valor de cada coisa relevante, atualizado a cada evento
(Cap 13 s.2: "nenhuma logica de DECISAO reside no Display Core" - guardar o
ultimo valor recebido nao e decisao, e so agregacao pra exibicao).

Varios dispositivos podem acessar ao mesmo tempo (Cap 13 s.4) - cada
conexao GET /eventos recebe os mesmos updates via Server-Sent Events.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from motion_core.behavior.behavior_core import MODO_MANUAL, MODOS_DE_CONTROLE
from motion_core.memory.api import MemoryAPI
from orion.kernel.config import ConfigurationManager
from orion.kernel.event_bus import Evento, EventBus

logger = logging.getLogger("motion_core.webui.server")

DIRETORIO_ESTATICO = Path(__file__).parent / "static"

#: enderecos considerados "local" para a pagina CONFIGURACAO (Cap 13 s.4:
#: "acesso restrito") - a pedido do usuario, restrito ao proprio
#: Raspberry, nem o resto da rede local ve essa pagina.
ENDERECOS_LOCAIS = frozenset({"127.0.0.1", "::1"})

#: Variavel de ambiente com a senha da pagina CONFIGURACAO. Vive em
#: config/secrets.env (gitignored), NUNCA no codigo: este repositorio e
#: publico no GitHub. Sem ela definida, o login por senha fica desligado e
#: a pagina segue restrita ao proprio Raspberry.
VARIAVEL_SENHA_CONFIG = "ORION_SENHA_CONFIG"
COOKIE_SESSAO = "orion_sessao"
#: teto de sessoes simultaneas - o robo tem um dono, nao mil.
MAXIMO_SESSOES = 8

#: topicos consumidos (Cap 13 s.7: system.*, motion.*, vision.*, voice.*,
#: navigation.*, diagnostic.* - mais safety.*, que a Fusao de Sensores
#: passou a publicar - Cap 12 s.8/Cap 18 s.9)
TOPICOS_REPASSADOS = (
    "system.ready",
    # Quem está no volante (2026-07-30). Vai pelo SSE para o botão do
    # dashboard mudar junto quando a troca vier de OUTRA aba, ou do próprio
    # robô - um botão que só sabe do clique dele mesmo mente sobre o estado.
    "behavior.modo_controle",
    "motion.status",
    "motion.position",
    "motion.scan_complete",
    "motion.obstacle_front",
    "navigation.mode_changed",
    "navigation.plan_created",
    "navigation.segment_started",
    "navigation.segment_completed",
    "navigation.obstacle_avoided",
    "navigation.target_lost",
    "navigation.error",
    "vision.person_detected",
    # A POSICAO DA CABECA nunca foi repassada (achado de 2026-07-28): o
    # pan/tilt se mexia e nada disso chegava na interface nem em qualquer
    # observador do barramento. Diagnosticar "o pan/tilt esta maluco" era
    # impossivel por construcao - uma sonda no /eventos media zero mesmo
    # com a cabeca girando. Mesmo padrao que ja mordeu 3 vezes neste
    # projeto: alguem publica e ninguem escuta, e publicar nao falha.
    "motion.pan_tilt",
    "vision.target_centered",
    "vision.person_lost",
    "voice.status",
    "diagnostic.error",
    "safety.safe_mode_entered",
    "safety.safe_mode_exited",
    "comm.mensagem.telemetry",
    "comm.link_degraded",
    "comm.module_lost",
    "comm.module_recovered",
    "memory.updated",
    # O maestro (EDR-0020) publica aqui qual comportamento esta no comando:
    # repouso, atendendo, vigilia, ia_estrategica, mentor. Ficou de fora da
    # lista ate 2026-07-25, entao as DECISOES do robo - a informacao mais
    # interessante que ele produz - nunca chegavam na interface. Dava a
    # impressao de maestro morto quando ele so era invisivel.
    "behavior.status",
    "sentinela.alerta",
)

#: Vocabulario FECHADO do controle manual. A interface nunca inventa
#: comando: o que nao estiver aqui e recusado antes de chegar ao Mega.
#: Movimento continua sujeito a seguranca reativa do firmware (Cap 18) -
#: este endpoint PEDE, nao manda.
#: Comandos que NÃO disputam o volante - valem em qualquer modo de controle.
#:
#: `STOP` está aqui por segurança, e é o item mais importante da lista: quem vê
#: o robô indo para um lugar ruim precisa poder pará-lo AGORA, sem antes
#: procurar em que modo o robô está nem apertar um botão de modo. Acender a luz
#: e ligar o ventilador entram porque não têm nada a ver com dirigir.
#:
#: Todo o resto (avançar, girar, ré) move as rodas e é recusado quando o
#: volante está com a IA - ver `_handler_comando`.
COMANDOS_SEM_VOLANTE = frozenset(
    {"STOP", "LIGHT_ON", "LIGHT_OFF", "FAN_ON", "FAN_OFF"}
)

COMANDOS_PERMITIDOS = frozenset(
    {
        "MOVE_FORWARD",
        "MOVE_CONTINUOUS",
        "TURN_LEFT",
        "TURN_RIGHT",
        "STOP",
        "LIGHT_ON",
        "LIGHT_OFF",
        "FAN_ON",
        "FAN_OFF",
        # Entrou com o joystick (2026-07-27): e a UNICA forma de dar re.
        # O firmware nao tem "MOVE_BACKWARD" continuo - `andarDistancia`
        # com distancia NEGATIVA e o caminho da re (motor_manager.h). Usar
        # distancia limitada em vez de movimento continuo tambem e mais
        # seguro aqui: a re NAO tem seguranca reativa (o SafetyManager so
        # olha o sensor frontal), entao um comando continuo dependeria de
        # alguem lembrar de mandar STOP.
        "MOVE_DISTANCE",
    }
)

#: Teto de re por comando. A re nao e coberta pela seguranca reativa do
#: Mega, entao cada pedido anda um pedaco curto e precisa ser repetido -
#: soltar o joystick simplesmente para de repetir.
RE_MAXIMA_CM = 15.0

#: Piso de velocidade: abaixo disto o motor de passo tranca em vez de girar.
VELOCIDADE_MINIMA_PERCENT = 20.0

#: quantas conversas mostrar na pagina CONVERSA por padrao (Cap 13 s.4)
CONVERSAS_PADRAO = 30

#: Teto do texto do chat. O campo da pagina ja limita, mas limite de
#: formulario e sugestao: quem garante e quem recebe.
CHAT_MAXIMO_CARACTERES = 500

#: Quanto esperar a resposta do Notebook. Generoso de proposito - o caminho
#: inteiro e modelo de linguagem + gravacao no banco, e o `behavior.aconselhar`
#: (25 s) e uma pergunta bem mais curta que uma conversa. Estourar aqui nao
#: perde a mensagem: ela ja chegou e sera gravada; so a resposta se perde.
CHAT_TIMEOUT_S = 60.0


#: quantos erros manter no painel de diagnostico (Cap 13 s.5)
MAXIMO_ERROS_RECENTES = 20
#: quantas linhas do fim do log expor via GET /log (Cap 13 s.4: "acesso ao log,
#: somente leitura")
LINHAS_LOG_PADRAO = 200
#: QUAL log a tela mostra (corrigido em 2026-07-30, achado pelo dono olhando a
#: propria pagina).
#:
#: Apontava para `data/logs/orion.log`, que e escrito pelo `python -m orion` -
#: um caminho de boot que NAO roda em producao (o Notebook sobe
#: `tools/conversar_fofao.py` pelo orion-avatar.service, e o Pi sobe
#: `python -m motion_core`). Medido no dia do achado:
#:
#:     orion.log        1.713 bytes, ultima escrita em 23/07, conteudo de 17/07
#:     motion_core.log  10,3 MB,     escrito no segundo em que se olhou
#:
#: Ou seja: a unica tela de diagnostico do robo mostrava 20 linhas de um boot
#: de duas semanas atras, dizendo "Etapa de boot ainda nao implementada", e
#: nunca uma linha do que o Fofao estava fazendo. Quem abrisse a pagina para
#: entender um problema de agora recebia um retrato morto - e sem erro nenhum,
#: porque o arquivo existia e era lido com sucesso.
#:
#: E o mesmo arquivo que o systemd alimenta (`StandardOutput=append:` em
#: tools/orion-motion.service), entao a tela passa a ver exatamente o que o
#: servico escreve, ao vivo.
CAMINHO_LOG = Path("data/logs/motion_core.log")

#: quantos eventos brutos manter no painel "ultimos eventos" (Cap 13 s.5)
MAXIMO_EVENTOS_RECENTES = 30

_INTERVALO_PING_S = 15.0

#: Arquivos que o navegador tem que REVALIDAR a cada carga (2026-07-30).
_EXTENSOES_SEM_CACHE = (".css", ".js", ".html")


@web.middleware
async def _revalidar_estaticos(request: web.Request, handler):
    """Manda `Cache-Control: no-cache` para o front do dashboard.

    POR QUE EXISTE. O `add_static` da aiohttp responde com `ETag` e
    `Last-Modified`, mas SEM `Cache-Control` - e sem essa diretiva o navegador
    decide por heuristica propria, servindo do cache sem nem perguntar ao
    servidor. Isso custou tempo de bancada real: o CSS novo do seletor de
    controle estava no ar (confirmado por `curl`) e o dono continuava vendo a
    tela antiga, perguntando *"cadê a cor quando selecionado"*. Diagnosticar
    interface pelo que o servidor tem, enquanto o navegador mostra outra coisa,
    e o mesmo tipo de mentira que este projeto passou o dia removendo dos dados.

    `no-cache` NAO e "nao guarde": e "guarde, mas revalide antes de usar". Com
    o ETag que a aiohttp ja envia, a revalidacao custa um 304 vazio - o arquivo
    so viaja de novo quando muda de verdade. Numa bancada em que o front muda
    varias vezes por sessao, esse e o trade certo.

    Nao pega o `/eventos` (SSE): a resposta dele e um StreamResponse ja enviado,
    e mexer no header depois do prepare() levantaria erro - por isso o filtro e
    por extensao, e nao um header global.
    """
    resposta = await handler(request)
    caminho = request.path.lower()
    if caminho == "/" or caminho.endswith(_EXTENSOES_SEM_CACHE):
        resposta.headers["Cache-Control"] = "no-cache"
    return resposta


class WebUIServer:
    def __init__(
        self,
        event_bus: EventBus,
        host: str = "0.0.0.0",
        port: int = 8080,
        memory_api: MemoryAPI | None = None,
        config: ConfigurationManager | None = None,
        maestro=None,
        comm=None,
    ) -> None:
        # memory_api e opcional (Cap 13 s.4: "consultas de historico...
        # locais ao SSD") - None quando o banco de dados nao esta
        # disponivel (ex.: SSD de producao nao montado nesta maquina de
        # dev), a pagina CONVERSA so mostra um aviso nesse caso em vez de
        # quebrar. config e opcional pela mesma razao - sem ele, a pagina
        # CONFIGURACAO so mostra um aviso.
        self._event_bus = event_bus
        self._host = host
        self._port = port
        self._memory_api = memory_api
        self._config = config
        #: O maestro (BehaviorCore) é o DONO do modo de controle - esta página
        #: só lê e pede a troca. Opcional pela mesma razão dos outros: sem ele
        #: o botão aparece desabilitado em vez de a página quebrar.
        #:
        #: Não guardamos cópia do modo aqui de propósito. O valor mostrado na
        #: tela é sempre lido do maestro na hora (ver `_handler_estado`): uma
        #: segunda cópia poderia discordar de quem de fato arbitra, e mostrar
        #: "AUTÔNOMO" numa tela cujo robô está em manual seria pior que não
        #: mostrar nada.
        self._maestro = maestro
        #: Link com o Mission Core (Notebook). So o CHAT usa - e o unico
        #: caminho daqui para a IA, que nao roda nesta maquina. Opcional pela
        #: mesma razao dos outros: sem ele a caixa de texto responde "sem
        #: link" em vez de a pagina quebrar.
        self._comm = comm
        self._clientes: set[web.StreamResponse] = set()
        self._runner: web.AppRunner | None = None
        #: sinaliza os handlers SSE de longa duracao (_handler_sse) para
        #: saírem do loop na hora do encerrar() - ver comentario la.
        self._encerrando = asyncio.Event()

        # estado agregado (Cap 13 s.5): so o ultimo valor de cada coisa,
        # nunca logica de decisao - so cache pra quem acabou de conectar
        self._estado: dict[str, Any] = {
            "sistema": {"robot_name": None, "modo": None},
            "navegacao": {"modo": None, "ultimo_plano": None},
            "hardware": {
                "estado": None,
                "em_movimento": None,
                "distancia_frontal_cm": None,
                "distancia_traseira_cm": None,
                "temperatura_c": None,
                "umidade_percent": None,
                "inclinacao_graus": None,
                "aceleracao_g": None,
                "impacto_detectado": None,
            },
            "posicao": None,
            "seguranca": {"safe_mode_ativo": False, "motivo": None},
            "visao": {"ultima_deteccao": None},
            "voz": {"estado": None},
            "mapa": {"leituras": []},
            "diagnostico": {"ultimos_erros": [], "modulos": {}},
        }
        self._eventos_recentes: list[dict[str, Any]] = []
        #: tokens de sessao da pagina CONFIGURACAO (memoria apenas -
        #: reiniciar o Motion Core desloga todo mundo, o que e desejavel)
        self._sessoes: set[str] = set()

        self._app = web.Application(middlewares=[_revalidar_estaticos])
        self._app.router.add_get("/eventos", self._handler_sse)
        self._app.router.add_get("/estado", self._handler_estado)
        self._app.router.add_post("/comando", self._handler_comando)
        self._app.router.add_post("/controle", self._handler_controle)
        self._app.router.add_post("/cabeca", self._handler_cabeca)
        self._app.router.add_get("/", self._handler_index)
        self._app.router.add_get("/mapa", self._handler_mapa)
        self._app.router.add_get("/diagnostico", self._handler_diagnostico)
        self._app.router.add_get("/log", self._handler_log)
        self._app.router.add_get("/conversa", self._handler_conversa)
        self._app.router.add_get("/api/conversas", self._handler_api_conversas)
        self._app.router.add_post(
            "/api/conversa/mensagem", self._handler_conversa_mensagem
        )
        self._app.router.add_get("/configuracao", self._handler_configuracao)
        self._app.router.add_get("/api/configuracao", self._handler_api_configuracao)
        self._app.router.add_post("/api/configuracao/entrar", self._handler_login_configuracao)
        self._app.router.add_static("/", DIRETORIO_ESTATICO, show_index=False)

        for topico in TOPICOS_REPASSADOS:
            event_bus.subscribe(topico, self._fazer_manipulador(topico))

    async def _handler_comando(self, request: web.Request) -> web.Response:
        """Controle manual: pede um comando ao Hardware Core, via navegacao.

        Existe porque ate 2026-07-25 a UNICA forma de comandar o robo era a
        voz. Uma transcricao errada deixava o operador sem NENHUM caminho
        para dar uma ordem - nem para mandar PARAR. Isso e falha de
        seguranca, nao so de conveniencia.

        Ate 2026-07-27 este handler falava com o Hardware Core DIRETO
        (comm.send), pulando o NavigationCore inteiro - violava a regra 1
        (toda comunicacao entre modulos passa pelo Event Bus) e tinha uma
        consequencia concreta: o modo de navegacao nunca virava MANUAL, o
        maestro (Behavior Core) nunca sabia que alguem tinha assumido o
        controle pelo dashboard, e um comportamento autonomo podia mandar
        comando por cima do joystick a qualquer momento - foi a causa real
        do joystick nao mover o robo no primeiro teste (2026-07-27). Agora
        publica em navigation.comando como qualquer outro modulo; o
        NavigationCore executa (com a checagem tatica de obstaculo,
        Cap 12 s.6) e o comportamento ControleManual usa o mesmo evento so
        para saber que o operador esta ativo e arbitrar com o maestro.

        A resposta HTTP confirma que o pedido foi ACEITO e encaminhado, nao
        que o robo ja se moveu - a execucao e assincrona. Falhas reais
        (obstaculo, Arduino ausente) chegam pelo canal de eventos que a
        pagina ja escuta via SSE (navigation.error, navigation.obstacle_avoided).

        Nao enfraquece nada: a seguranca reativa do Mega continua vetando
        movimento enquanto houver perigo, e o vocabulario e fechado.
        """
        try:
            corpo = await request.json()
        except Exception:
            return web.json_response({"erro": "corpo precisa ser JSON"}, status=400)

        comando = str(corpo.get("comando", "")).strip().upper()
        if comando not in COMANDOS_PERMITIDOS:
            return web.json_response(
                {
                    "erro": "comando nao permitido",
                    "permitidos": sorted(COMANDOS_PERMITIDOS),
                },
                status=400,
            )
        # QUEM ESTÁ NO VOLANTE MANDA (2026-07-30).
        #
        # Recusar aqui, na FONTE, e não deixar o comando entrar no barramento
        # para ser ignorado depois: um comando publicado e engolido em silêncio
        # foi exatamente a família de bug que este botão veio resolver. Além
        # disso, o `NavigationCore` executa `navigation.comando` sem consultar
        # modo nenhum - se o pedido chegasse lá, o robô se moveria mesmo com o
        # volante entregue à IA.
        #
        # A resposta diz o que fazer, em vez de só negar: o 409 aparece na tela
        # como aviso, e o operador entende que precisa pedir o volante.
        if comando not in COMANDOS_SEM_VOLANTE and not self.operador_tem_o_volante():
            return web.json_response(
                {
                    "erro": "o volante esta com a IA - toque em MANUAL para assumir",
                    "modo_controle": self.modo_controle,
                    "comando": comando,
                },
                status=409,
            )

        payload: dict[str, Any] = {"comando": comando}

        # Velocidade: ate 2026-07-27 este endpoint DESCARTAVA o que o cliente
        # mandasse e o firmware caia no default de 50% - com joystick isso
        # significa que empurrar pouco ou muito dava exatamente a mesma
        # velocidade. Limitada pelo motion.max_speed_percent do orion.yaml
        # (regra 6: o teto e config, nao numero no codigo) e por um piso,
        # abaixo do qual o motor de passo tranca em vez de girar.
        if "velocidade_percent" in corpo:
            try:
                velocidade = float(corpo["velocidade_percent"])
            except (TypeError, ValueError):
                return web.json_response({"erro": "velocidade_percent invalida"}, status=400)
            teto = 100.0
            if self._config is not None:
                teto = float(self._config.secao("motion")["max_speed_percent"])
            payload["velocidade_percent"] = max(
                VELOCIDADE_MINIMA_PERCENT, min(velocidade, teto)
            )

        # Distancia so faz sentido no MOVE_DISTANCE, e o unico uso hoje e a
        # re do joystick. Limitada em modulo: a re NAO tem seguranca reativa
        # (o SafetyManager so olha o sensor frontal), entao cada pedido anda
        # um pedaco curto e precisa ser repetido - soltar o joystick
        # simplesmente para de repetir, e o robo para sozinho.
        if comando == "MOVE_DISTANCE":
            try:
                distancia = float(corpo.get("distancia_cm", 0.0))
            except (TypeError, ValueError):
                return web.json_response({"erro": "distancia_cm invalida"}, status=400)
            if distancia == 0.0:
                return web.json_response({"erro": "distancia_cm obrigatoria"}, status=400)
            payload["distancia_cm"] = max(-RE_MAXIMA_CM, min(distancia, RE_MAXIMA_CM))

        await self._event_bus.publish("navigation.comando", {"acao": "MANUAL", **payload})
        return web.json_response({"ok": True, "comando": comando})

    async def _handler_index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(DIRETORIO_ESTATICO / "index.html")

    async def _handler_mapa(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(DIRETORIO_ESTATICO / "mapa.html")

    async def _handler_diagnostico(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(DIRETORIO_ESTATICO / "diagnostico.html")

    async def _handler_log(self, request: web.Request) -> web.Response:
        # Cap 13 s.4: "acesso ao log, somente leitura" - so le e devolve,
        # nunca aceita escrita/comando (regra arquitetural #1: nenhuma
        # logica mora no Display Core).
        if not CAMINHO_LOG.exists():
            return web.json_response({"linhas": [], "aviso": "nenhum log encontrado ainda"})
        try:
            linhas_pedidas = int(request.query.get("linhas", LINHAS_LOG_PADRAO))
        except ValueError:
            linhas_pedidas = LINHAS_LOG_PADRAO
        linhas_pedidas = max(1, min(linhas_pedidas, 2000))
        with CAMINHO_LOG.open("r", encoding="utf-8", errors="replace") as arquivo:
            todas_as_linhas = arquivo.readlines()
        return web.json_response({"linhas": todas_as_linhas[-linhas_pedidas:], "aviso": None})

    async def _handler_conversa(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(DIRETORIO_ESTATICO / "conversa.html")

    async def _handler_conversa_mensagem(self, request: web.Request) -> web.Response:
        """Chat por texto (2026-08-03): o que o dono digita chega a IA.

        Pedido dele: *"vai q estou em outro lugar e quero mandar ele ir na
        cozinha"*. Ate hoje a UNICA entrada de linguagem era a voz - e voz
        exige estar perto do robo, que e exatamente a situacao que este
        endereco resolve.

        Nao decide nada aqui. O texto atravessa o link ate o Notebook
        (`mission.conversa` -> `PonteConversa`), que o entrega ao MESMO
        `processar_comando` de uma transcricao. Por isso o turno digitado e
        gravado em `conversas` e alimenta a memoria igual ao falado: e a
        mesma conversa, so mudou a porta de entrada.

        A resposta HTTP traz o texto do robo para a tela mostrar. Se o
        Notebook nao responder a tempo, a mensagem NAO se perdeu - ela ja
        chegou e ja foi processada la; o que se perde e so a resposta de
        volta, e a pagina recarrega o historico do banco para mostrar o que
        de fato houve.
        """
        if self._comm is None:
            return web.json_response(
                {"erro": "sem link com o Mission Core - o chat precisa do Notebook"},
                status=503,
            )
        try:
            corpo = await request.json()
        except Exception:
            return web.json_response({"erro": "corpo precisa ser JSON"}, status=400)

        texto = str(corpo.get("texto", "")).strip()
        if not texto:
            return web.json_response({"erro": "mensagem vazia"}, status=400)
        if len(texto) > CHAT_MAXIMO_CARACTERES:
            return web.json_response(
                {"erro": f"mensagem acima de {CHAT_MAXIMO_CARACTERES} caracteres"},
                status=400,
            )

        try:
            resposta = await self._comm.request(
                "mission_core",
                {"comando": "mission.conversa", "texto": texto},
                timeout_s=CHAT_TIMEOUT_S,
            )
        except Exception:
            logger.warning("chat: Mission Core nao respondeu", exc_info=True)
            return web.json_response(
                {"erro": "o Fofao nao respondeu a tempo"}, status=504
            )

        payload = resposta.payload or {}
        if not payload.get("ok"):
            return web.json_response(
                {"erro": payload.get("erro", "o Fofao nao conseguiu responder")},
                status=502,
            )
        return web.json_response({"ok": True, "resposta": payload.get("resposta", "")})

    async def _handler_api_conversas(self, request: web.Request) -> web.Response:
        # Cap 13 s.4: "consultas de historico... locais ao SSD" - le direto
        # da memoria (Fase 3), sem passar pelo Event Bus, ja que o
        # historico completo nao vive em memoria RAM deste servidor.
        if self._memory_api is None:
            return web.json_response({"conversas": [], "aviso": "banco de dados nao disponivel"})
        try:
            limite = int(request.query.get("limite", CONVERSAS_PADRAO))
        except ValueError:
            limite = CONVERSAS_PADRAO
        limite = max(1, min(limite, 200))
        conversas = await self._memory_api.recall("conversas", limite=limite)
        return web.json_response({"conversas": list(reversed(conversas)), "aviso": None})

    @staticmethod
    def _acesso_local_permitido(request: web.Request) -> bool:
        return request.remote in ENDERECOS_LOCAIS

    def _acesso_permitido(self, request: web.Request) -> bool:
        """Do proprio Raspberry, ou com sessao aberta por senha.

        O acesso local continua liberado sem senha de proposito: quem ja esta
        no teclado do robo passou por uma barreira fisica, e exigir senha ali
        so atrapalharia a manutencao.
        """
        if self._acesso_local_permitido(request):
            return True
        token = request.cookies.get(COOKIE_SESSAO)
        return bool(token) and token in self._sessoes

    async def _handler_login_configuracao(self, request: web.Request) -> web.Response:
        """Abre sessao para a pagina CONFIGURACAO a partir da rede.

        A senha vem de ORION_SENHA_CONFIG (config/secrets.env, gitignored) -
        NUNCA do codigo: este repositorio e publico no GitHub, e uma senha
        commitada esta publicada.

        Sem a variavel definida, o login fica desligado e a pagina segue
        restrita ao proprio Raspberry, como era antes.
        """
        senha_esperada = os.environ.get(VARIAVEL_SENHA_CONFIG, "")
        if not senha_esperada:
            return web.json_response(
                {"erro": "acesso por senha nao configurado neste robo"}, status=403
            )
        try:
            corpo = await request.json()
        except Exception:
            return web.json_response({"erro": "corpo precisa ser JSON"}, status=400)

        # compare_digest em vez de "==": comparacao comum sai mais cedo na
        # primeira letra errada, e esse tempo vaza a senha aos poucos.
        if not hmac.compare_digest(str(corpo.get("senha", "")), senha_esperada):
            return web.json_response({"erro": "senha incorreta"}, status=401)

        token = secrets.token_urlsafe(32)
        self._sessoes.add(token)
        if len(self._sessoes) > MAXIMO_SESSOES:
            self._sessoes.pop()  # teto simples: o robo tem um dono, nao mil
        resposta = web.json_response({"ok": True})
        resposta.set_cookie(
            COOKIE_SESSAO, token, httponly=True, samesite="Strict", max_age=8 * 3600
        )
        return resposta

    async def _handler_configuracao(self, request: web.Request) -> web.StreamResponse:
        # A PAGINA agora e sempre servida; quem exige credencial e a API de
        # dados abaixo. Antes o 403 vinha antes do HTML carregar, e nao havia
        # onde mostrar um campo de senha.
        return web.FileResponse(DIRETORIO_ESTATICO / "configuracao.html")

    async def _handler_api_configuracao(self, request: web.Request) -> web.Response:
        if not self._acesso_permitido(request):
            return web.json_response({"erro": "acesso restrito"}, status=403)
        if self._config is None:
            return web.json_response({"parametros": {}, "aviso": "configuracao nao disponivel"})
        return web.json_response({"parametros": self._config.bruto(), "aviso": None})

    @property
    def modo_controle(self) -> str | None:
        """Quem está no volante, lido do maestro na hora. None = sem maestro."""
        if self._maestro is None:
            return None
        return self._maestro.modo_controle

    def operador_tem_o_volante(self) -> bool:
        """True quando o joystick pode comandar.

        Sem maestro (ex.: máquina de dev sem Behavior Core) devolve True: a
        interface volta a ser o que era antes deste botão existir, em vez de
        ficar inutilizável.
        """
        return self._maestro is None or self._maestro.modo_controle == MODO_MANUAL

    #: Limites fisicos do pan/tilt, iguais aos do orion.yaml (vision).
    #: Duplicar numero e ruim, mas o Motion Core nao le a secao `vision` - ela
    #: e do Notebook. Ficam aqui com o mesmo valor e um teste que os prende:
    #: melhor um numero repetido e conferido que um servo forcado contra o
    #: batente, que foi o que derrubou o ESP32 inteiro em 2026-08-03.
    PAN_MIN, PAN_MAX = -80.0, 80.0
    TILT_MIN, TILT_MAX = -30.0, 45.0

    @staticmethod
    def _limitar(valor: float, minimo: float, maximo: float) -> float:
        return max(minimo, min(maximo, valor))

    async def _handler_cabeca(self, request: web.Request) -> web.Response:
        """O JOYSTICK DA CABECA (2026-08-03).

        Pedido do dono: *"poem um joystick para mover a cabeca tambem, assim
        posso olhar para todos os lados"* - o caso de uso e olhar a casa de
        longe pela camera, sem depender de o robo decidir para onde olhar.

        ANGULO ABSOLUTO, e nao incremento. A posicao do punho E a posicao da
        cabeca. Incremento acumularia erro a cada comando perdido pela rede e a
        interface passaria a discordar do servo - que e exatamente a doenca que
        o pan/tilt ja tem (dois donos comandando por caminhos independentes,
        sem nunca ressincronizar). Absoluto nao deriva: cada comando corrige o
        anterior.

        Publica no MESMO topico que os comportamentos usam (`motion.pan_tilt`),
        e nao por um caminho novo. Um segundo caminho ate os servos seria um
        terceiro dono da cabeca, e o projeto ja paga caro por ter dois.
        """
        if not self._acesso_permitido(request):
            return web.json_response({"erro": "acesso negado"}, status=403)
        try:
            dados = await request.json()
            pan = float(dados["pan"])
            tilt = float(dados["tilt"])
        except (ValueError, KeyError, TypeError):
            return web.json_response({"erro": "envie pan e tilt numericos"}, status=400)

        pan = self._limitar(pan, self.PAN_MIN, self.PAN_MAX)
        tilt = self._limitar(tilt, self.TILT_MIN, self.TILT_MAX)
        await self._event_bus.publish("motion.pan_tilt", {"pan": pan, "tilt": tilt})
        return web.json_response({"ok": True, "pan": pan, "tilt": tilt})

    async def _handler_controle(self, request: web.Request) -> web.Response:
        """O BOTÃO: entrega o volante ao operador ou ao robô.

        POR QUE ESTE ENDPOINT EXISTE (2026-07-30). Até hoje quem comandava era
        decidido pela tabela de prioridades do maestro, e ninguém tinha
        escolhido isso - era consequência. A IA (prio 40) perdia para o
        joystick (95) e para a vigilância (100), e o pior: perdia EM SILÊNCIO,
        porque o comando de quem tinha prioridade maior sobrescrevia a decisão
        dela na vaga única do NavigationCore. Medido no log: 5444 "IA decidiu"
        e zero manobras executadas.

        Pedido do dono: *"vamos colocar um botao para escolher q controla, se o
        joistk ou a ia, autonomo ou manual"*, e *"ai passa a responsabilidade
        por escolha"*. É disso que se trata - a escolha passa a ser explícita e
        de quem tem o direito de fazê-la, em vez de emergir de um empate de
        números.
        """
        if self._maestro is None:
            return web.json_response(
                {"erro": "sem Behavior Core nesta instancia - nao ha volante a entregar"},
                status=503,
            )
        try:
            corpo = await request.json()
        except Exception:
            return web.json_response({"erro": "corpo precisa ser JSON"}, status=400)

        modo = str(corpo.get("modo", "")).strip().lower()
        if modo not in MODOS_DE_CONTROLE:
            return web.json_response(
                {"erro": "modo invalido", "permitidos": list(MODOS_DE_CONTROLE)},
                status=400,
            )

        await self._maestro.definir_modo_controle(modo)
        logger.info("webui: volante entregue ao modo '%s' pelo dashboard", modo)
        return web.json_response({"ok": True, "modo_controle": modo})

    async def _handler_estado(self, request: web.Request) -> web.Response:
        # O modo entra no MESMO payload que a página já busca, em vez de um
        # endpoint só para ele: assim o botão nunca aparece desenhado num
        # estado diferente do resto da tela.
        estado = dict(self._estado)
        estado["controle"] = {
            "modo": self.modo_controle,
            "operador_no_volante": self.operador_tem_o_volante(),
        }
        # So a PORTA: o host quem monta e o navegador, a partir do
        # endereco que ele ja usou para abrir esta pagina. Assim o mesmo
        # dashboard serve na LAN e pelo Tailscale sem reconfiguracao.
        conf_web = self._config.secao("display")["web"] if self._config else {}
        estado["camera"] = {"porta": conf_web.get("camera_porta")}
        return web.json_response(
            {"estado": estado, "eventos_recentes": self._eventos_recentes}
        )

    async def _handler_sse(self, request: web.Request) -> web.StreamResponse:
        resposta = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await resposta.prepare(request)
        self._clientes.add(resposta)
        try:
            while True:
                # BUG REAL (2026-07-27, vistoria pos-gravacao de firmware):
                # `await asyncio.sleep(_INTERVALO_PING_S)` puro nao acorda
                # por sinal nenhum - so pelo relogio. `AppRunner.cleanup()`
                # (chamado em encerrar()) espera os handlers em andamento
                # TERMINAREM SOZINHOS antes de retornar; com um cliente
                # conectado de verdade (o proprio dashboard no navegador),
                # este handler nunca termina sozinho - e um `while True`
                # de proposito, para manter o SSE vivo. Resultado medido:
                # o desligamento gracioso do Motion Core travava ate 90s
                # (o padrao do systemd), so resolvido por SIGKILL.
                # `wait_for` sobre `self._encerrando` faz o mesmo papel do
                # sleep, mas acorda NA HORA quando encerrar() sinaliza -
                # mesmo padrao ja usado em BehaviorCore.executar() para o
                # tick do maestro.
                try:
                    await asyncio.wait_for(self._encerrando.wait(), timeout=_INTERVALO_PING_S)
                    break  # encerrando foi sinalizado - sai sem mandar mais ping
                except TimeoutError:
                    pass  # timeout normal - hora do ping
                await resposta.write(b": ping\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._clientes.discard(resposta)
        return resposta

    def _fazer_manipulador(self, topico: str):
        async def _manipular(evento: Evento) -> None:
            dados = self._extrair_dados(topico, evento)
            self._atualizar_estado(topico, dados)
            self._registrar_evento_recente(topico, dados)
            await self._transmitir(topico, dados)

        return _manipular

    @staticmethod
    def _extrair_dados(topico: str, evento: Evento) -> dict[str, Any]:
        # TELEMETRY nao passa pela normalizacao de topico das EVENT (Cap 14
        # s.7) - o dado real fica aninhado em "payload" (mesmo detalhe ja
        # documentado em NavigationCore/FusaoSensores).
        if topico == "comm.mensagem.telemetry":
            return evento.dados.get("payload", {})
        return evento.dados

    def _atualizar_estado(self, topico: str, dados: dict[str, Any]) -> None:
        if topico == "system.ready":
            self._estado["sistema"] = {
                "robot_name": dados.get("robot_name"),
                "modo": dados.get("modo"),
            }
        elif topico == "navigation.mode_changed":
            self._estado["navegacao"]["modo"] = dados.get("modo")
        elif topico in (
            "navigation.plan_created",
            "navigation.segment_started",
            "navigation.segment_completed",
            "navigation.obstacle_avoided",
        ):
            self._estado["navegacao"]["ultimo_plano"] = {"evento": topico, **dados}
        elif topico == "motion.status":
            self._estado["hardware"]["estado"] = dados.get("estado")
        elif topico == "motion.position":
            self._estado["posicao"] = dados
        elif topico == "motion.scan_complete":
            self._estado["mapa"] = {"leituras": dados.get("leituras", [])}
        elif topico == "comm.mensagem.telemetry":
            hardware = self._estado["hardware"]
            for campo in (
                "estado",
                "em_movimento",
                "distancia_frontal_cm",
                "distancia_traseira_cm",
                "temperatura_c",
                "umidade_percent",
                "inclinacao_graus",
                "aceleracao_g",
                "impacto_detectado",
                # Bussola QMC6310 (2026-07-27). `rumo_valido` importa tanto
                # quanto o rumo: sem calibracao o valor existe mas nao
                # significa nada, e a tela nao pode mostrar 217 graus como se
                # fosse direcao de verdade.
                "bussola_conectada",
                "bussola_calibrada",
                "bussola_calibrando",
                "rumo_valido",
                "rumo_graus",
                "campo_ut",
                # `sem_eco` separa "nada refletiu" de distancia medida - sem
                # isto a tela mostra 517cm como se fosse parede (ver
                # sensor_ultrassonico.h e EDR-0024).
                "frontal_sem_eco",
                "traseiro_sem_eco",
                # Amostras descartadas por cegueira do loop durante o eco
                # (2026-07-27, sensor_ultrassonico.h) - contador ACUMULADO
                # desde o boot, para achar quem bloqueia o loop (hoje, o
                # envio da telemetria), nao para acusar sensor ruim.
                "frontal_descartes",
                "traseiro_descartes",
            ):
                # `if campo in dados` tambem cobre o pacote REDUZIDO que o
                # firmware manda com o robo em movimento: campo ausente
                # mantem o ultimo valor conhecido em vez de zerar a tela.
                if campo in dados:
                    hardware[campo] = dados[campo]
        elif topico == "safety.safe_mode_entered":
            self._estado["seguranca"] = {"safe_mode_ativo": True, "motivo": dados.get("motivo")}
        elif topico == "safety.safe_mode_exited":
            self._estado["seguranca"] = {"safe_mode_ativo": False, "motivo": None}
        elif topico == "vision.person_detected":
            self._estado["visao"]["ultima_deteccao"] = dados
        elif topico == "voice.status":
            self._estado["voz"]["estado"] = dados.get("estado")
        elif topico in ("diagnostic.error", "comm.link_degraded"):
            self._registrar_erro(topico, dados)
        elif topico == "comm.module_lost":
            self._atualizar_saude_modulo(dados.get("modulo"), "perdido")
        elif topico == "comm.module_recovered":
            self._atualizar_saude_modulo(dados.get("modulo"), "ok")

    def _registrar_erro(self, topico: str, dados: dict[str, Any]) -> None:
        erros = self._estado["diagnostico"]["ultimos_erros"]
        erros.append({"topico": topico, "dados": dados, "timestamp": time.time()})
        if len(erros) > MAXIMO_ERROS_RECENTES:
            erros.pop(0)

    def _atualizar_saude_modulo(self, nome: str | None, status: str) -> None:
        if not nome:
            return
        self._estado["diagnostico"]["modulos"][nome] = {"status": status, "timestamp": time.time()}

    def _registrar_evento_recente(self, topico: str, dados: dict[str, Any]) -> None:
        self._eventos_recentes.append({"topico": topico, "dados": dados, "timestamp": time.time()})
        if len(self._eventos_recentes) > MAXIMO_EVENTOS_RECENTES:
            self._eventos_recentes.pop(0)

    async def _transmitir(self, topico: str, dados: dict[str, Any]) -> None:
        linha = f"event: {topico}\ndata: {json.dumps(dados)}\n\n".encode()
        for cliente in list(self._clientes):
            try:
                await cliente.write(linha)
            except (ConnectionResetError, RuntimeError):
                self._clientes.discard(cliente)

    async def iniciar(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info("Interface web servida em http://%s:%d", self._host, self._port)

    async def encerrar(self) -> None:
        # Sinaliza PRIMEIRO: precisa acontecer antes do cleanup() para os
        # handlers SSE em andamento acordarem e saírem do loop - ver
        # comentario em _handler_sse.
        self._encerrando.set()
        if self._runner is not None:
            await self._runner.cleanup()
