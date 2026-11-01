"""Modelo de mundo unificado do Jarvis (base da integração cognitiva).

PROBLEMA QUE RESOLVE
--------------------
Até aqui cada comportamento do maestro assinava seus próprios eventos e via
só um pedaço da realidade: `Vigilia` sabia de rostos mas não de bateria,
`VigilanciaObstaculo` sabia de obstáculo mas não de quem estava na frente.
Decisão boa exige o quadro inteiro.

Esta classe assina os tópicos do Event Bus (regra arquitetural #1: ninguém
chama ninguém direto) e mantém UM retrato coerente do mundo, que qualquer
comportamento consulta.

O PONTO MAIS IMPORTANTE: DADO VELHO NÃO É DADO
----------------------------------------------
Um modelo de mundo ingênuo guarda o último valor recebido para sempre. Isso
é perigoso: se a telemetria parar (cabo solto, Mega resetando), o quadro
continuaria dizendo "obstáculo a 34cm, tudo ok" com informação de cinco
minutos atrás, e o maestro decidiria em cima de ficção.

Aqui cada dado tem prazo de validade (`validade_s`). Passou do prazo, o
campo responde `None` - "eu não sei" - em vez de mentir um valor velho.
Quem consulta precisa tratar `None`, e isso é de propósito: força quem
decide a encarar a incerteza em vez de ignorá-la.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("motion_core.behavior.estado_do_mundo")

# Prazo de validade por família de dado. Vem do ritmo real de cada fonte:
# a telemetria do Mega chega a cada 500ms, então 3s já significa "parou de
# chegar". Visão e voz são naturalmente esporádicas e valem por mais tempo.
VALIDADE_TELEMETRIA_S = 3.0
#: Quanto tempo uma varredura do LiDAR continua valendo como "agora".
#:
#: O gerenciador publica a ~2,5 Hz, entao 3 s e mais de sete varreduras de
#: folga: se nada chegou nesse tempo, o sensor parou, e distancia velha num
#: robo que anda e pior que distancia nenhuma - e o mesmo raciocinio da
#: telemetria, com o mesmo numero de propósito.
VALIDADE_LIDAR_S = 3.0
VALIDADE_ESTADO_HARDWARE_S = 5.0
VALIDADE_VISAO_S = 15.0
VALIDADE_VOZ_S = 30.0
#: Por quanto tempo "andei sem medida confiavel" continua sendo noticia para
#: quem decide. 30s cobre alguns segmentos de movimento; passado isso, o fato
#: envelheceu e o silencio volta a ser a resposta honesta.
VALIDADE_RISCO_S = 30.0


@dataclass
class _Campo:
    """Um valor com o instante em que chegou."""

    valor: Any = None
    instante: float = 0.0

    def ler(self, validade_s: float, agora: float | None = None) -> Any:
        """Devolve o valor, ou None se estiver velho demais para confiar."""
        if self.instante == 0.0:
            return None  # nunca chegou nada
        agora = time.monotonic() if agora is None else agora
        if agora - self.instante > validade_s:
            return None  # venceu
        return self.valor

    def escrever(self, valor: Any) -> None:
        self.valor = valor
        self.instante = time.monotonic()


#: As 8 direcoes, na convencao do projeto (0 esquerda, 90 frente, 180 direita).
DIRECOES_8 = (
    ("frente", 90.0),
    ("frente-direita", 135.0),
    ("direita", 180.0),
    ("trás-direita", 225.0),
    ("trás", 270.0),
    ("trás-esquerda", 315.0),
    ("esquerda", 0.0),
    ("frente-esquerda", 45.0),
)

#: Meia largura do corpo para o corredor: 0,36 m de bitola medida na fita +
#: rodas = ~0,40 m; 20 cm de cada lado do eixo do movimento.
MEIA_LARGURA_CORPO_CM = 20.0


def livre_no_corredor(raios_cm, eixo_graus: float, meia_largura_cm: float) -> float | None:
    """Quanto da para andar na direcao `eixo_graus` antes de um ponto do
    LiDAR entrar no CORREDOR da largura do corpo (2026-11-01).

    E isto, e nao a menor distancia de uma fatia, que diz o que e obstaculo:
    parede ao lado num corredor nao bate; caixa na frente bate. None = nenhum
    ponto medido no corredor (livre ate o alcance, ou perto demais/vidro).
    """
    menor: float | None = None
    for angulo, distancia in enumerate(raios_cm or ()):
        if distancia is None:
            continue
        desvio = math.radians(angulo - eixo_graus)
        avanco = distancia * math.cos(desvio)
        if avanco > 0 and abs(distancia * math.sin(desvio)) < meia_largura_cm:
            if menor is None or avanco < menor:
                menor = avanco
    return menor


@dataclass(frozen=True)
class RetratoDoMundo:
    """Foto imutável do mundo num instante, para quem for decidir.

    Todo campo pode ser None e isso SEMPRE significa "não sei" - ou nunca
    chegou, ou o dado venceu. Nunca significa "zero" nem "tudo bem".
    """

    # --- corpo do robô (telemetria do Mega) ---
    obstaculo_frente_cm: float | None
    obstaculo_tras_cm: float | None
    bateria_v: float | None
    bateria_nivel: str | None

    # --- situação (estado do Hardware/Motion Core) ---
    estado_hardware: str | None

    # --- mundo lá fora (visão, no Notebook) ---
    pessoa_presente: bool | None
    pessoa_nome: str | None
    pessoa_conhecida: bool | None
    alerta_sentinela: str | None

    # --- interação (voz) ---
    ouvindo: bool

    # --- meta ---
    telemetria_viva: bool  # False = paramos de receber notícias do corpo

    #: True quando o robô avançou HÁ POUCO sem medida frontal confiável.
    #:
    #: Nasce da decisão do dono de 2026-07-30: *"travas de segurança só
    #: impedem o robô de andar - retira as travas, temos os sensores para
    #: realizar estudos de risco e tomada de decisão"*. A navegação deixou de
    #: recusar o avanço na dúvida; em troca, ela DECLARA o que fez. Este campo
    #: é onde essa declaração chega a quem decide.
    #:
    #: Sem ele, o `navigation.risco_assumido` seria mais um evento publicado
    #: no vazio - e o defeito que este projeto mais repete é exatamente esse.
    #: Default False para não quebrar quem constrói o retrato à mão (testes).
    risco_frontal_assumido: bool = False

    # --- OS SENTIDOS QUE A IA NAO CONHECIA (2026-11-01) ---
    # O dono, vendo o robo bater: "ele nao sabe usar a bussola como
    # referencia, nao sabe que tem um sensor de pontos para se guiar, nao
    # usa a visao computacional". Os sensores mediam; o retrato e que so
    # contava frente e tras. Agora conta os quatro lados do LiDAR, o rumo
    # da bussola, a posicao pelo encoder e o que a camera ve AGORA.
    obstaculo_esquerda_cm: float | None = None
    obstaculo_direita_cm: float | None = None
    rumo_graus: float | None = None
    posicao_x_m: float | None = None
    posicao_y_m: float | None = None
    #: "classe (lado)" de cada objeto que a camera viu nos ultimos segundos.
    objetos_vistos: tuple = ()
    #: (direcao, cm ou None) para as 8 direcoes - quanto ele pode ANDAR em
    #: cada uma antes de algo bater no corpo. Ver `livre_no_corredor`.
    livre_por_direcao: tuple = ()

    def resumo(self) -> str:
        """Uma linha legível - útil em log e para explicar decisão."""
        def fmt(v: Any, sufixo: str = "") -> str:
            return "?" if v is None else f"{v}{sufixo}"

        return (
            f"frente={fmt(self.obstaculo_frente_cm, 'cm')} "
            f"bat={fmt(self.bateria_nivel)} "
            f"estado={fmt(self.estado_hardware)} "
            f"pessoa={fmt(self.pessoa_nome)} "
            f"ouvindo={self.ouvindo} "
            f"corpo={'vivo' if self.telemetria_viva else 'SEM SINAL'}"
        )


class EstadoDoMundo:
    """Funde os eventos do barramento num retrato único e datado."""

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus

        self._telemetria = _Campo()
        self._lidar = _Campo()
        self._estado_hardware = _Campo()
        self._pessoa = _Campo()
        self._alerta = _Campo()
        self._risco_frente = _Campo()
        self._posicao = _Campo()
        self._raios = _Campo()
        #: classe -> (lado, instante). Ver `_ao_ver_objeto`.
        self._objetos: dict[str, tuple[str, float]] = {}
        self._voz_ativa = False

        # Corpo do robô: telemetria periódica vinda do Mega pela ponte serial.
        event_bus.subscribe("comm.mensagem.telemetry", self._ao_receber_telemetria)
        # O QUE O ROBÔ VÊ EM VOLTA (2026-09-19). Sem esta linha, a distância
        # que chegava na IA vinha dos ultrassons - que foram RETIRADOS do robô.
        #
        # O LiDAR funciona desde 15/09 e publica `lidar.scan` a 2,5 Hz, mas
        # ninguém entre ele e a IA escutava: a navegação usava, a interface
        # web mostrava, e o retrato do mundo continuava perguntando ao sensor
        # que não existe mais. A IA estratégica foi DESLIGADA em 07/08 porque
        # o contexto dela dizia "NÃO SEI a distância" e a única saída sensata
        # era "olhar" - 505 chamadas, 4 decisões, robô parado. Esta assinatura
        # é o que tira aquele motivo do caminho.
        event_bus.subscribe("lidar.scan", self._ao_receber_lidar)
        # Situação do Hardware Core (IDLE / OBSTACLE_DETECTED / SAFE_MODE...).
        event_bus.subscribe("motion.status", self._ao_receber_estado)
        # Mundo lá fora: visão roda no Notebook e chega encaminhada.
        #
        # DOIS eventos, de propósito (corrigido em 2026-07-29): eles respondem
        # perguntas diferentes e só juntos dão o quadro.
        #
        #   person_detected   -> "tem ALGUÉM aí" (qualquer pessoa que o YOLO vê)
        #   person_recognized -> "é o João Paulo" (só quando o rosto casa com
        #                        alguém cadastrado)
        #
        # Até esta data só o primeiro era assinado, e `_ao_ver_pessoa` lia
        # `evento.dados["nome"]` dele - campo que aquele evento NUNCA carregou
        # (o payload real é `{classe, confianca, caixa, timestamp}`). Então
        # `pessoa_conhecida` era falso para todo mundo, inclusive para os donos
        # da casa, e a IA decidia achando que só entravam estranhos.
        event_bus.subscribe("vision.person_detected", self._ao_ver_pessoa)
        event_bus.subscribe("vision.person_recognized", self._ao_reconhecer_pessoa)
        event_bus.subscribe("vision.person_lost", self._ao_perder_pessoa)
        event_bus.subscribe("sentinela.alerta", self._ao_receber_alerta)
        # Objetos que a camera ve (cadeira, tv, gato...) - chegam do Notebook
        # encaminhados, estrangulados la a 1/s por classe (2026-11-01).
        event_bus.subscribe("vision.object_detected", self._ao_ver_objeto)
        # Pose pela roda (encoder) com rumo fundido a bussola - mesmo processo.
        event_bus.subscribe("motion.position", self._ao_receber_posicao)
        # A navegação declarando que andou sem saber o que havia à frente.
        # CONFERIDO ANTES DE ASSINAR (a regra que este projeto aprendeu a
        # duras penas): `navigation.risco_assumido` é publicado pelo
        # `NavigationCore`, que roda NESTE processo e neste mesmo barramento -
        # não precisa de encaminhamento e não vira órfão.
        event_bus.subscribe("navigation.risco_assumido", self._ao_assumir_risco)
        # Interação por voz.
        event_bus.subscribe("voice.wake_detected", self._ao_acordar_voz)
        event_bus.subscribe("voice.response_finished", self._ao_terminar_voz)

    # ----- entrada de eventos -----

    async def _ao_receber_lidar(self, evento: Evento) -> None:
        """Guarda a varredura em setores. Quem interpreta e `_distancia_em`."""
        setores = evento.dados.get("setores")
        if setores is not None:
            self._lidar.escrever(setores)
        raios = evento.dados.get("raios_cm")
        if raios and len(raios) == 360:
            self._raios.escrever(list(raios))

    #: Meia-largura da janela que conta como FRENTE e como TRAS, em graus.
    #:
    #: 60 casa com `lidar_frontal_angulos_graus: [-60, 60]` do orion.yaml, que
    #: a navegacao usa. O numero mora nos dois lugares porque o retrato e a
    #: navegacao sao donos de perguntas diferentes - mas se um dia divergirem
    #: de proposito, que seja por escolha e nao por esquecimento.
    _MEIA_JANELA_GRAUS = 60.0
    #: Convencao de angulo do projeto (igual a do mapa.js e a de
    #: `NavigationCore._angulo_na_frente`): 0° = esquerda, 90° = FRENTE,
    #: 180° = direita, 270° = TRAS.
    _GRAUS_FRENTE = 90.0
    _GRAUS_TRAS = 270.0
    _GRAUS_ESQUERDA = 0.0
    _GRAUS_DIREITA = 180.0

    def _distancia_em(self, setores, centro_graus: float) -> float | None:
        """Menor distancia medida na janela em torno de `centro_graus`.

        None = NAO SEI, nunca "esta livre". Setor sem eco (`distancia_cm`
        nulo) e PULADO, nao contado como espaco vazio: desde 2026-09-19 a
        varredura sai mesmo incompleta, entao setor vazio virou rotina e
        confundi-lo com caminho aberto seria a falha classica deste projeto -
        e a mesma que o ultrassom cometia quando nada refletia.

        Atencao ao que "sem eco" inclui: alem do alcance, vidro, angulo
        rasante - e PERTO DEMAIS, abaixo do `min_distancia_mm` do LiDAR. O
        obstaculo colado no robo some da varredura igual ao horizonte vazio.
        """
        if not setores:
            return None
        menor: float | None = None
        for setor in setores:
            distancia_cm = setor.get("distancia_cm")
            if distancia_cm is None:
                continue
            centro = (
                float(setor.get("inicio_graus", 0.0)) + float(setor.get("fim_graus", 0.0))
            ) / 2.0
            delta = abs((centro - centro_graus + 180.0) % 360.0 - 180.0)
            if delta > self._MEIA_JANELA_GRAUS:
                continue
            if menor is None or distancia_cm < menor:
                menor = float(distancia_cm)
        return menor

    async def _ao_receber_telemetria(self, evento: Evento) -> None:
        payload = evento.dados.get("payload") or {}
        self._telemetria.escrever(payload)

        # O ESTADO DO CORPO VEM DA TELEMETRIA TAMBÉM (2026-07-30).
        #
        # É a mesma correção que o `NavigationCore._ao_atualizar_telemetria`
        # recebeu em 29/07, e que este arquivo não recebeu junto. O
        # `motion.status` (abaixo) só é publicado pelo firmware QUANDO O
        # ESTADO MUDA - com o Mega parado em OBSTACLE_DETECTED não há
        # transição nenhuma para anunciar, o campo vence por idade e o retrato
        # passa a dizer `None`.
        #
        # A consequência era visível em produção, uma linha por ciclo no
        # prompt de quem decide as manobras:
        #
        #     - estado do corpo: não sei (sem registro)
        #
        # A IA precisava escolher a manobra sem saber se o corpo estava
        # ocioso, executando uma missão ou travado num obstáculo - e o dado
        # certo chegava aqui a cada segundo, dentro deste mesmo payload, sem
        # ninguém ler. A telemetria é o lastro que corrige a deriva; o
        # `motion.status` continua servindo para reagir na hora da transição.
        estado = payload.get("estado")
        if estado:
            self._estado_hardware.escrever(estado)

    async def _ao_receber_estado(self, evento: Evento) -> None:
        estado = evento.dados.get("estado")
        if estado is not None:
            self._estado_hardware.escrever(estado)

    async def _ao_ver_pessoa(self, evento: Evento) -> None:
        """Alguém apareceu. Este evento NÃO diz quem - ver _ao_reconhecer_pessoa.

        Não sobrescreve um nome já conhecido: a visão publica `person_detected`
        a cada quadro para cada pessoa, e `person_recognized` só quando o rosto
        é identificado. Se este handler apagasse o nome, a identidade duraria
        um quadro e o robô esqueceria quem está na frente dele a 15 vezes por
        segundo.
        """
        atual = self._pessoa.ler(VALIDADE_VISAO_S) or {}
        self._pessoa.escrever(
            {
                "nome": atual.get("nome"),
                "conhecida": bool(atual.get("nome")),
            }
        )

    async def _ao_reconhecer_pessoa(self, evento: Evento) -> None:
        """Agora sim sabemos QUEM é - só este evento carrega `nome`."""
        nome = evento.dados.get("nome")
        self._pessoa.escrever({"nome": nome, "conhecida": bool(nome)})

    async def _ao_perder_pessoa(self, evento: Evento) -> None:
        # Vence na hora: sumiu de vista, não há mais ninguém presente.
        self._pessoa = _Campo()

    #: Largura do quadro da camera (640x480): divide em terços para dizer
    #: de que lado o objeto esta. Com a cabeca centrada, e o lado do ROBO.
    _LARGURA_QUADRO = 640.0

    async def _ao_ver_objeto(self, evento: Evento) -> None:
        classe = evento.dados.get("classe")
        caixa = evento.dados.get("caixa") or ()
        if not classe:
            return
        lado = "à frente"
        if len(caixa) == 4:
            centro = (float(caixa[0]) + float(caixa[2])) / 2.0
            if centro < self._LARGURA_QUADRO / 3:
                lado = "à esquerda"
            elif centro > 2 * self._LARGURA_QUADRO / 3:
                lado = "à direita"
        self._objetos[str(classe)] = (lado, time.monotonic())

    async def _ao_receber_posicao(self, evento: Evento) -> None:
        self._posicao.escrever(dict(evento.dados))

    async def _ao_receber_alerta(self, evento: Evento) -> None:
        self._alerta.escrever(evento.dados.get("tipo", "desconhecido"))

    async def _ao_assumir_risco(self, evento: Evento) -> None:
        """O robô andou sem medida confiável - e agora quem decide sabe."""
        logger.info(
            "estado do mundo: risco assumido em '%s' (%s)",
            evento.dados.get("contexto", "?"),
            evento.dados.get("motivo", "?"),
        )
        self._risco_frente.escrever(True)

    async def _ao_acordar_voz(self, evento: Evento) -> None:
        self._voz_ativa = True

    async def _ao_terminar_voz(self, evento: Evento) -> None:
        self._voz_ativa = False

    # ----- leitura -----

    def retrato(self) -> RetratoDoMundo:
        """Monta a foto do mundo agora, já descartando o que venceu."""
        agora = time.monotonic()
        tele = self._telemetria.ler(VALIDADE_TELEMETRIA_S, agora) or {}
        viva = self._telemetria.ler(VALIDADE_TELEMETRIA_S, agora) is not None
        pessoa = self._pessoa.ler(VALIDADE_VISAO_S, agora)

        def do_mega(chave: str, chave_valido: str | None = None) -> Any:
            """Lê campo da telemetria respeitando o flag de validade dele.

            O firmware manda pares (valor, valor_valido). Ignorar o flag aqui
            reintroduziria exatamente o tipo de mentira que esta classe
            existe para evitar.

            JÁ NÃO SERVE PARA DISTÂNCIA (2026-09-19): os ultrassons saíram do
            robô e quem mede é o LiDAR, logo abaixo. O terceiro parâmetro
            (`chave_sem_eco`) foi embora com eles - existia só para desmascarar
            o `_foraDeAlcance` do `sensor_ultrassonico.h`, que reportava o teto
            de alcance (517 cm) com `valido: true` quando nada refletia.
            Sobrou aqui o que o corpo de fato mede: bateria.
            """
            if not viva:
                return None
            if chave_valido is not None and not tele.get(chave_valido):
                return None
            return tele.get(chave)

        # A DISTÂNCIA VEM DO LIDAR (2026-09-19). Mesma regra de sempre: só vale
        # se for recente (`VALIDADE_LIDAR_S`), e None é "não sei", nunca
        # "está livre" - ver `_distancia_em`.
        setores = self._lidar.ler(VALIDADE_LIDAR_S, agora)
        posicao = self._posicao.ler(VALIDADE_TELEMETRIA_S, agora)
        raios = self._raios.ler(VALIDADE_LIDAR_S, agora)

        return RetratoDoMundo(
            obstaculo_frente_cm=self._distancia_em(setores, self._GRAUS_FRENTE),
            obstaculo_tras_cm=self._distancia_em(setores, self._GRAUS_TRAS),
            bateria_v=do_mega("bateria_tensao_v", "bateria_lida"),
            bateria_nivel=do_mega("bateria_nivel", "bateria_lida"),
            estado_hardware=self._estado_hardware.ler(VALIDADE_ESTADO_HARDWARE_S, agora),
            pessoa_presente=None if pessoa is None else True,
            pessoa_nome=None if pessoa is None else pessoa.get("nome"),
            pessoa_conhecida=None if pessoa is None else pessoa.get("conhecida"),
            alerta_sentinela=self._alerta.ler(VALIDADE_VISAO_S, agora),
            ouvindo=self._voz_ativa,
            telemetria_viva=viva,
            risco_frontal_assumido=bool(self._risco_frente.ler(VALIDADE_RISCO_S, agora)),
            obstaculo_esquerda_cm=self._distancia_em(setores, self._GRAUS_ESQUERDA),
            obstaculo_direita_cm=self._distancia_em(setores, self._GRAUS_DIREITA),
            rumo_graus=do_mega("rumo_graus", "rumo_valido"),
            posicao_x_m=(posicao or {}).get("x_m"),
            posicao_y_m=(posicao or {}).get("y_m"),
            livre_por_direcao=(
                tuple(
                    (nome, livre_no_corredor(raios, eixo, MEIA_LARGURA_CORPO_CM))
                    for nome, eixo in DIRECOES_8
                )
                if raios
                else ()
            ),
            objetos_vistos=tuple(
                f"{classe} ({lado})"
                for classe, (lado, instante) in sorted(self._objetos.items())
                if agora - instante <= VALIDADE_VISAO_S
            ),
        )
