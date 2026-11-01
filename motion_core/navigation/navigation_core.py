"""Motion Core / Navegação (Cap 12) — roda no Raspberry Pi.

Principio (Cap 12 s.2): o Notebook decide (missao), o Raspberry navega
(este modulo), o Arduino so executa comandos simples e devolve telemetria.
Este modulo nunca fala com o Arduino direto por transporte - so por meio
do ComunicacaoService (comm.send/request), igual qualquer outro modulo
(Cap 14 s.7).

Sem hardware fisico montado ainda (motores/sensores - ver docs/journal.md)
- toda a logica daqui e validada em nivel de protocolo, contra
`tools/sim_hardware_core.py` ou um FakeTransporte de teste, nunca com movimento
real. Mesmo padrao ja usado nas Fases 4 e 5.

Escopo desta primeira versao: HOLD, MANUAL, GOTO e PATROL estao completos
(maquina de estados, seguranca tatica de obstaculo, eventos do Cap 12 s.11).
FOLLOW e EXPLORE estao numa versao minima deliberada - ver os comentarios
nas funcoes correspondentes - porque dependem de dados que so existem com
o Vision Core rodando de verdade (FOLLOW) ou de um algoritmo de exploracao
mais elaborado que fica para uma proxima iteracao (EXPLORE).
"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any

from orion.communication.service import ComunicacaoService, ErroComunicacao
from orion.kernel.event_bus import EventBus, Evento, Prioridade

from motion_core.navigation.desvio_local import AVANCAR, GIRAR, escolher_passo

logger = logging.getLogger("motion_core.navigation.navigation_core")

DESTINO_HARDWARE = "hardware_core"

#: intervalo de checagem e tempo maximo esperando o Hardware Core voltar a
#: IDLE antes de enviar o proximo comando de movimento de um segmento.
#: Achado real testando com o Mega fisico: o ACK de um COMMAND so confirma
#: que ele foi *recebido*, nao que o movimento anterior terminou (o
#: firmware ACKa na hora e executa MOVE_DISTANCE/TURN_* de forma
#: assincrona) - encadear comandos de movimento sem esperar Estado::IDLE
#: fez o Mega as vezes nao ACKar o comando seguinte.
INTERVALO_CHECAGEM_OCIOSO_S = 0.1
TIMEOUT_AGUARDAR_OCIOSO_S = 10.0

#: tempo maximo de espera pelo motion.scan_complete apos SCAN_FRONT (Cap 12
#: s.7: varredura 0-180 graus). Achado real testando com o Mega fisico: a
#: varredura leva ~2.1s (7 angulos x 300ms de assentamento do servo,
#: radar_manager.h) - mandar o proximo comando logo apos o ACK do SCAN_FRONT
#: (que so confirma que a varredura *comecou*) colide com esse periodo e o
#: Mega as vezes nao ACKa o comando seguinte. Esperar o evento de verdade
#: resolve os dois problemas de uma vez: usa a leitura real (como a spec
#: pede) e nao sobrepoe comandos com a varredura em andamento.
TIMEOUT_SCAN_S = 3.0
#: folga extra apos o motion.scan_complete - ver comentario em
#: _escanear_a_frente().
PAUSA_APOS_SCAN_S = 0.3
#: faixa de angulos considerada "a frente" nas leituras do radar (Cap 12 s.7
#: varre 0-180; graus 90 = centro/frente do robo)
ANGULOS_FRONTAIS = (60, 90, 120)

#: Quanto o robo anda entre duas varreduras, ao explorar.
#:
#: Fica CURTO de proposito, e nao deve crescer: e a distancia que o robo percorre
#: as cegas entre duas medidas da frente. Andar mais por passo nao e "explorar
#: mais", e olhar menos.
DISTANCIA_SEGMENTO_EXPLORE_CM = 30.0

#: Quantos passos o robo da por DECISAO de explorar (2026-07-30).
#:
#: Ate hoje era 1, e o dono viu o resultado na bancada: *"vi o rele acionar,
#: indica q o robo resolveu andar porem pouco"*. O numero explica o sintoma - a
#: IA decide a cada `intervalo_s: 30` do orion.yaml e cada decisao rendia UM
#: segmento de 30 cm, ou seja **1 cm/s de media**. Para atravessar uma sala de 4
#: metros o robo precisaria de mais de 6 minutos, decidindo 13 vezes.
#:
#: O ganho vem de andar um TRECHO por decisao, nao de andar mais por passo: cada
#: passo continua sendo precedido de varredura, e o primeiro sinal de obstaculo
#: encerra o trecho na hora. A malha fechada e a mesma; o que muda e nao ficar
#: 30 s parado esperando a proxima consulta para dar o passo seguinte.
#:
#: 4 x 30 cm = 1,2 m por decisao. Teto conservador de propósito enquanto o
#: deslocamento real nao e medido (nao ha encoder de roda - ver
#: `fusao_sensores.py`): sem odometria confiavel, quem sabe onde o robo esta e o
#: radar a cada 30 cm, e nao a conta de passos.
SEGMENTOS_POR_EXPLORE = 40
#: (2026-11-01 noite) Eram 6 e o trecho acabava em ~30 s; depois o robo ficava
#: parado ate a proxima consulta da IA (5 min). A IA escolhe EXPLORAR uma vez -
#: intencao - e o corpo segue andando e desviando ate ficar sem saida, chegar
#: um comando ou completar os 40 passos.
#: (2026-11-01) Eram 4 avancos; agora cada passo pode ser um AVANCO ou um GIRO
#: de desvio (ver desvio_local.py), entao 6 passos cobrem ~4 avancos com dois
#: desvios no meio.

#: Depois de um giro de desvio, quanto esperar para a varredura do LiDAR ja
#: refletir a nova direcao. A janela do gerenciador guarda ~3 voltas (~350 ms)
#: e o minimo-vence faz o obstaculo de antes do giro "assombrar" o angulo
#: errado ate sair dela.
ESPERA_VARREDURA_APOS_GIRO_S = 0.6
#: Varredura mais velha que isto nao serve para decidir um passo.
IDADE_MAXIMA_VARREDURA_S = 1.5
#: Quanto esperar o corpo COMECAR um movimento recem-mandado. Logo apos o
#: envio o estado ainda e IDLE, e `_aguardar_ocioso` passava direto - medido
#: em 01/11 17:31: dois giros de 120 graus e dois avancos sairam no mesmo
#: segundo, cada um atropelando o anterior.
ESPERA_INICIO_MOVIMENTO_S = 1.5

#: comandos manuais que o modo MANUAL repassa direto ao Hardware Core
#: (Cap 10 s.5) - qualquer outro nome e rejeitado com navigation.error.
#: MOVE_CONTINUOUS, MOVE_DISTANCE, FAN_ON e FAN_OFF entraram em 2026-07-27
#: para bater com o vocabulario que a webui ja aceitava (COMANDOS_PERMITIDOS
#: em motion_core/webui/server.py) - antes o joystick e o /comando falavam
#: com o Hardware Core direto, sem passar por aqui, entao a lista nunca
#: precisou cobrir esses comandos.
COMANDOS_MANUAIS = frozenset(
    {
        "MOVE_FORWARD",
        "MOVE_CONTINUOUS",
        "MOVE_DISTANCE",
        "TURN_LEFT",
        "TURN_RIGHT",
        "STOP",
        "LIGHT_ON",
        "LIGHT_OFF",
        "FAN_ON",
        "FAN_OFF",
        "DOCK",
        # Auto-ajuste (2026-07-28, pedido do usuario: calibracao ele tem que
        # fazer, se auto ajustar). Nao movem o robo - mandam o Mega refazer a
        # referencia dos proprios sensores. O CALIBRATE_IMU saiu em
        # 2026-08-18 junto com a MPU6050.
        "CALIBRATE_COMPASS",
    }
)
#: destes, quais avancam o robo para frente - sao os unicos sujeitos a
#: checagem de obstaculo antes de serem enviados (Cap 12 s.6, camada tatica).
#: MOVE_DISTANCE fica de fora de proposito: e o comando da re do joystick
#: (distancia negativa), e a re nao tem sensor traseiro na seguranca reativa
#: (SafetyManager so olha o radar frontal) - checar obstaculo A FRENTE antes
#: de andar PARA TRAS não faria sentido.
COMANDOS_QUE_AVANCAM = frozenset({"MOVE_FORWARD", "MOVE_CONTINUOUS"})
#: comandos manuais que movimentam o robo (giram rodas) - esperam o Hardware
#: Core ficar ocioso antes de seguir, para nao empilhar sobre um movimento em
#: andamento (mesmo motivo do GOTO/PATROL, ver INTERVALO_CHECAGEM_OCIOSO_S).
COMANDOS_DE_MOVIMENTO = COMANDOS_QUE_AVANCAM | frozenset(
    {"MOVE_DISTANCE", "TURN_LEFT", "TURN_RIGHT"}
)

#: sentidos aceitos pela acao GIRAR (ver NavigationCore.girar). Nomes em
#: portugues de proposito: quem publica isto e o maestro, nao o firmware.
SENTIDOS_DE_GIRO = {"ESQUERDA": "TURN_LEFT", "DIREITA": "TURN_RIGHT"}


class ModoNavegacao(str, Enum):
    HOLD = "HOLD"
    MANUAL = "MANUAL"
    GOTO = "GOTO"
    PATROL = "PATROL"
    FOLLOW = "FOLLOW"
    EXPLORE = "EXPLORE"


class ErroNavegacao(Exception):
    """Comando/missao invalida ou navegacao bloqueada por seguranca."""


class NavigationCore:
    def __init__(
        self,
        event_bus: EventBus,
        comm: ComunicacaoService,
        config_motion: dict[str, Any],
        config_navigation: dict[str, Any],
        consultar_repeticoes=None,
    ) -> None:
        self._event_bus = event_bus
        self._comm = comm
        self._velocidade_padrao = config_motion["max_speed_percent"]
        # O DESVIO DO EXPLORE USA OS MESMOS NUMEROS DA TRAVA (2026-11-01): o
        # limite (50 cm, dono) e a meia-largura do corredor. Se fossem
        # diferentes, o desvio escolheria um passo que a trava recusa.
        conf_trava = config_motion.get("trava_lidar", {}) or {}
        self._limite_desvio_cm = float(conf_trava.get("distancia_cm", 50.0))
        self._meia_largura_desvio_cm = float(conf_trava.get("meia_largura_cm", 25.0))
        #: Os 360 raios da ultima varredura (cm ou None), para o desvio local.
        self._raios_lidar: list | None = None
        self._raios_recebido_em: float | None = None
        self._distancia_seguranca_frontal_cm = config_motion["min_front_distance_cm"]
        self._timeout_comando_s = config_motion["mission_timeout_s"]
        self._scan_antes_de_segmento = config_navigation["patrol_scan_before_segment"]
        self._tentativas_obstaculo_max = config_navigation["obstacle_retry_max"]
        self._follow_lost_timeout_s = config_navigation["follow_lost_timeout_s"]
        self._velocidade_reduzida_perto_pessoa = config_navigation[
            "reduced_speed_near_person_percent"
        ]
        #: Ciclo fechado de experiencias (Camada 6): consulta injetada que
        #: conta quantas vezes a situacao ja falhou na janela. None = o degrau
        #: fica desligado e o robô repete como antes (comportamento padrao).
        #: Assinatura: `await consultar_repeticoes(situacao, janela_s) -> int`.
        self._consultar_repeticoes = consultar_repeticoes
        self._recorrente_janela_s = config_navigation.get("obstacle_recorrente_janela_s", 300.0)
        self._recorrente_limite = config_navigation.get("obstacle_recorrente_limite", 2)

        # LiDAR LD06 (Motion Core, USB serial - decisao do dono 2026-08-17).
        # `lidar_frontal_angulos_graus` e a janela em torno da frente (90° na
        # convencao da web): [-60, 60] = os 120° a frente. A checagem tatica
        # ganha um sensor continuo de 360° que vê obstaculos laterais antes de
        # eles entrarem no ultrassom.
        angulos = config_navigation.get("lidar_frontal_angulos_graus", [-60, 60])
        self._lidar_frontal_limite_graus = (float(angulos[0]), float(angulos[1]))
        self._lidar_min_obstaculo_cm = float(
            config_navigation.get("lidar_min_obstaculo_cm", 25.0)
        )
        self._setores_lidar: list[dict[str, Any]] | None = None

        self._modo = ModoNavegacao.HOLD
        self._distancia_frontal_atual_cm: float | None = None
        #: O sensor frontal deu sinal de vida? None = nunca chegou telemetria.
        #:
        #: Separa dois "nao sei" que o projeto tratava como um so, e que pedem
        #: respostas opostas (`frontal_sensor_ok` no telemetry_manager.h):
        #:
        #:   False -> o modulo nao respondeu ao trigger: ausente, fio solto,
        #:            sem 5V. Exigir medida aqui deixaria o robo parado para
        #:            sempre em qualquer bancada sem ultrassom montado, e o
        #:            projeto inteiro tolera hardware ausente (Cap 6 s.8).
        #:   True  -> o sensor esta vivo e ainda assim nao mediu. ESTE e o caso
        #:            perigoso e medido: o eco de um obstaculo a 40 cm dura
        #:            ~2,3 ms e cabe inteiro na cegueira de ate 124 ms do loop
        #:            do Mega. Quanto mais PERTO, maior a chance de virar
        #:            "nao medi" - e com o EDR-0027 nao ha mais freio embaixo.
        self._frontal_sensor_ok: bool | None = None
        self._tarefa_follow: asyncio.Task | None = None
        self._ultima_deteccao_follow_s: float = 0.0
        self._scan_pendente: asyncio.Future[list[dict[str, Any]]] | None = None
        self._estado_hardware = "IDLE"
        #: Comando aguardando execucao. E UM SO, de proposito: o ULTIMO VENCE.
        #: Ver _ao_receber_comando para o porque.
        self._comando_pendente: dict[str, Any] | None = None
        self._tarefa_comando: asyncio.Task | None = None

        event_bus.subscribe("navigation.comando", self._ao_receber_comando)
        event_bus.subscribe("comm.mensagem.telemetry", self._ao_atualizar_telemetria)
        event_bus.subscribe("vision.person_recognized", self._ao_reconhecer_pessoa)
        event_bus.subscribe("motion.scan_complete", self._ao_completar_scan)
        event_bus.subscribe("motion.status", self._ao_atualizar_estado_hardware)
        event_bus.subscribe("lidar.scan", self._ao_atualizar_lidar)
        event_bus.subscribe("lidar.error", self._ao_falhar_lidar)

    @property
    def modo(self) -> ModoNavegacao:
        return self._modo

    def registrar_consulta_repeticoes(self, consulta) -> None:
        """Liga o degrau do ciclo fechado apos a memoria existir.

        A navegacao nasce antes do banco de producao (o SSD so aparece no
        passo 4 do __main__), entao a consulta e registrada aqui quando a
        MemoryAPI esta no ar. Assinatura da consulta:
        `await consulta(situacao, janela_s) -> int` (contagem de falhas).
        """
        self._consultar_repeticoes = consulta

    # ---------- telemetria / seguranca tatica ----------

    async def _ao_atualizar_telemetria(self, evento: Evento) -> None:
        # mensagens TELEMETRY nao passam pela normalizacao de topico que as
        # EVENT tem (Cap 14 s.7) - chegam com o envelope completo, o dado
        # real fica aninhado em "payload" (Mensagem.to_dict()).
        payload = evento.dados.get("payload", {})

        # O ESTADO DO MEGA VEM DA TELEMETRIA, NAO SO DA BORDA (2026-07-29).
        #
        # `motion.status` so e publicado pelo firmware QUANDO O ESTADO MUDA
        # (main.cpp, notificarTransicaoDeEstado). Isso deixava dois buracos:
        # se aquele unico evento se perdesse na serial, o valor velho valia
        # para sempre; e depois de um restart do Motion Core o Pi assumia o
        # `"IDLE"` inicial abaixo - um valor INVENTADO - ate o Mega mudar de
        # estado por conta propria. Com o Mega parado em OBSTACLE_DETECTED nao
        # ha transicao nenhuma para anunciar, entao o Pi acreditava em IDLE
        # indefinidamente enquanto o dado certo chegava na telemetria a cada
        # segundo, sem ninguem escutar.
        #
        # A telemetria carrega `estado` em TODO pacote. Ela vira o lastro que
        # corrige deriva; o `motion.status` continua servindo para reagir na
        # hora, sem esperar o proximo pacote.
        estado = payload.get("estado")
        if estado:
            self._estado_hardware = estado

        # A DISTANCIA FRONTAL NAO VEM MAIS DAQUI (2026-09-19).
        #
        # Os ultrassons foram retirados do robo fisicamente, e quem mede a
        # frente agora e o LiDAR STL-50B2 - ver `_ao_atualizar_lidar`, que
        # preenche `_distancia_frontal_atual_cm`. A telemetria do corpo segue
        # trazendo estado, bateria e sinal de vida; distancia, nao.
        #
        # O bloco que existia aqui lia `distancia_frontal_cm`,
        # `distancia_frontal_valida` e `frontal_sem_eco`. Ele foi embora junto
        # com o sensor - deixar o codigo lendo chaves que o firmware nao manda
        # mais faria a distancia congelar no ultimo valor lido e envelhecer em
        # silencio, que e pior que nao ter sensor.
        #
        # A LICAO QUE NAO SE PERDE: "nao medi" nunca pode virar "esta livre".
        # O ultrassom errava justamente para o lado perigoso (obstaculo perto
        # demais reportado como caminho aberto, medido em 29/07) e foi por isso
        # que `frente_desconhecida()` existe, separado de `_obstaculo_a_frente`.
        # O LiDAR tem a MESMA armadilha por outro caminho: ponto abaixo de
        # `min_distancia_mm` e descartado, entao perto demais tambem vira setor
        # sem eco. Os dois sensores mudam; o contrato do "nao sei", nao.
        if "frontal_sensor_ok" in payload:
            self._frontal_sensor_ok = bool(payload.get("frontal_sensor_ok"))

    def frente_desconhecida(self) -> bool:
        """True quando nao ha medida frontal confiavel agora.

        Existe separado de `_obstaculo_a_frente` de proposito: "nao sei" e
        "esta livre" eram a mesma resposta ate 2026-07-29, e essa confusao e o
        que fazia o robo tratar o proprio ponto cego como caminho aberto.
        """
        return self._distancia_frontal_atual_cm is None

    def _obstaculo_a_frente(self) -> bool:
        if self._distancia_frontal_atual_cm is None:
            # Sem medida confiavel. NAO responde "livre": ate 2026-07-29 este
            # `False` se apoiava na camada reativa do Arduino como rede de
            # seguranca ("o Mega para se eu errar"), e essa rede deixou de
            # existir no EDR-0027 - o Mega agora informa e nao freia. Quem
            # pergunta deve consultar `frente_desconhecida()` antes de
            # concluir qualquer coisa a partir deste False.
            return False
        return self._distancia_frontal_atual_cm < self._distancia_seguranca_frontal_cm

    # ---------- LiDAR LD06 (sensor do Pi, decisao do dono 2026-08-17) ----------

    async def _ao_atualizar_lidar(self, evento: Evento) -> None:
        """Guarda a varredura E alimenta a distancia frontal.

        O LIDAR VIROU A FONTE DA DISTANCIA FRONTAL (2026-09-19), no lugar do
        ultrassom, que foi retirado do robo fisicamente. Nao ha sensor novo a
        acomodar aqui: `_distancia_frontal_atual_cm` sempre foi a UNICA
        variavel de distancia frontal desta classe, e tudo que decide sobre
        andar - `frente_desconhecida`, `_obstaculo_a_frente`, o risco
        declarado - le dela. Trocar quem a preenche troca o sensor do robo
        inteiro sem mexer em nenhuma dessas decisoes.

        `None` continua significando "nao sei", nunca "esta livre": quando
        nenhum setor frontal tem medida, `_menor_distancia_lidar_frontal`
        devolve None e `frente_desconhecida()` passa a valer - exatamente o
        mesmo contrato que o `frontal_sem_eco` do ultrassom tinha.
        """
        raios = evento.dados.get("raios_cm")
        if raios and len(raios) == 360:
            self._raios_lidar = list(raios)
            self._raios_recebido_em = time.monotonic()
        setores = evento.dados.get("setores")
        if setores is not None:
            self._setores_lidar = setores
            self._distancia_frontal_atual_cm = self._menor_distancia_lidar_frontal()
            # SENSOR VIVO, ainda que a frente esteja sem eco. Esta linha e o
            # que separa os dois "nao sei" (ver `_avancar_e_seguro`): sensor
            # calado = anda e avisa no log; sensor FALANDO mas sem medida =
            # estudo de risco, com varredura e `navigation.risco_assumido`.
            # Vinha do `frontal_sensor_ok` do ultrassom; sem ela, depois que
            # o sensor saiu do robo, o sinal ficaria None para sempre e o
            # estudo de risco nunca mais aconteceria - silenciosamente.
            self._frontal_sensor_ok = True

    async def _ao_falhar_lidar(self, evento: Evento) -> None:
        """`lidar.error`: o sensor parou de responder.

        Derruba a medida junto: manter os ultimos setores faria o robo andar
        apoiado numa varredura de quando o sensor ainda funcionava. `False`
        (e nao None) diz "ja falou comigo e parou", que e o caso em que o log
        precisa gritar mais alto.

        Este topico era ORFAO ate 2026-09-19 - publicado pelo gerenciador do
        LiDAR e escutado por ninguem, o defeito que mais se repete neste
        projeto. Agora que o LiDAR e o unico sensor de distancia, ignorar o
        aviso de falha dele deixou de ser barato.
        """
        self._frontal_sensor_ok = False
        self._setores_lidar = None
        self._distancia_frontal_atual_cm = None
        logger.warning(
            "LiDAR falhou (%s) - o robo esta sem medida de distancia",
            evento.dados.get("motivo", "sem motivo"),
        )

    def _angulo_na_frente(self, angulo_graus: float) -> bool:
        """O angulo cai dentro da janela frontal configurada?

        Convencao igual a da web (mapa.js): 0° = esquerda, 90° = frente,
        180° = direita. A janela e relativa a frente, entao o limite nao
        muda quando o robo gira. Diferenca circular lida com o salto 0/360.
        """
        delta = (angulo_graus - 90.0) % 360.0
        if delta > 180.0:
            delta = 360.0 - delta
        minimo, maximo = self._lidar_frontal_limite_graus
        return minimo <= delta <= maximo

    def _menor_distancia_lidar_frontal(self) -> float | None:
        """Menor distancia medida nos setores frontais, ou None.

        Setor sem medida (`distancia_cm: null`) NAO e obstaculo: o LiDAR
        nao enxergou nada ali, e afirmar parede onde nao ha medida seria o
        mesmo fail-closed que este projeto combate.

        Mas tambem NAO e caminho livre: setor pulado nao entra no minimo, e
        se NENHUM setor frontal tiver medida o retorno e None - que
        `frente_desconhecida()` traduz em "nao sei". Ate 2026-09-19 a duvida
        era resolvida pelo ultrassom; ele saiu do robo, e agora quem resolve e
        a varredura (`SCAN_FRONT`) e a camera.
        """
        if self._setores_lidar is None:
            return None
        menor: float | None = None
        for setor in self._setores_lidar:
            distancia_cm = setor.get("distancia_cm")
            if distancia_cm is None:
                continue
            centro = (
                float(setor.get("inicio_graus", 0.0)) + float(setor.get("fim_graus", 0.0))
            ) / 2.0
            if not self._angulo_na_frente(centro):
                continue
            if menor is None or distancia_cm < menor:
                menor = distancia_cm
        return menor

    def _obstaculo_no_lidar(self) -> bool:
        """True quando algum setor frontal mediu mais perto que o limite.

        Sem LiDAR (varredura nunca publicada) responde False - tolerancia a
        ausencia (Cap 6 s.8). ATENCAO ao que esse False significa desde
        2026-09-19: o ultrassom saiu do robo, entao nao ha mais um segundo
        sensor tatico por tras. Quem pergunta tem que consultar
        `frente_desconhecida()` para separar "medi e esta livre" de "nao tenho
        sensor nenhum" - as duas respostas saem False aqui.
        """
        menor = self._menor_distancia_lidar_frontal()
        return menor is not None and menor < self._lidar_min_obstaculo_cm

    async def _ao_completar_scan(self, evento: Evento) -> None:
        if self._scan_pendente is not None and not self._scan_pendente.done():
            self._scan_pendente.set_result(evento.dados.get("leituras", []))

    async def _escanear_a_frente(self) -> list[dict[str, Any]]:
        """Manda SCAN_FRONT e espera o motion.scan_complete de verdade (nao
        so o ACK, que so confirma que a varredura comecou - ver TIMEOUT_SCAN_S).
        Em timeout, segue com lista vazia (a checagem por telemetria continua
        valendo como rede de seguranca)."""
        # OLHAR COM O CORPO PARADO (varredura de 2026-08-04).
        #
        # `_executar_segmento` ja esperava o corpo ficar ocioso antes de
        # varrer, mas os outros dois caminhos ate aqui nao: o EXPLORE chama
        # este metodo direto no inicio de cada passo, e o `SCAN_FRONT` avulso
        # (voz/Ronda) tambem. O passo anterior do EXPLORE termina mandando
        # MOVE_DISTANCE e publicando `segment_completed` NA HORA - o ACK so diz
        # que o comando chegou, nao que o robo parou. Resultado: a varredura
        # do passo seguinte saia com o robo ainda andando.
        #
        # Duas consequencias, as duas ja conhecidas deste projeto: as leituras
        # descrevem um lugar por onde o robo ja passou, e comando sobreposto a
        # movimento e exatamente o caso em que o firmware as vezes nao ACKa o
        # seguinte (ver INTERVALO_CHECAGEM_OCIOSO_S).
        #
        # Dentro de `_escanear_a_frente` de proposito, e nao em cada chamador:
        # quem varre quer o dado bom, sempre. Onde a espera ja acontecia antes,
        # esta chamada nao custa nada.
        await self._aguardar_ocioso()
        loop = asyncio.get_running_loop()
        self._scan_pendente = loop.create_future()
        await self._comm.send(DESTINO_HARDWARE, {"comando": "SCAN_FRONT"})
        try:
            leituras = await asyncio.wait_for(self._scan_pendente, timeout=TIMEOUT_SCAN_S)
        except TimeoutError:
            logger.warning("motion.scan_complete nao chegou em %.1fs apos SCAN_FRONT", TIMEOUT_SCAN_S)
            leituras = []
        finally:
            self._scan_pendente = None
        # achado real testando com o Mega fisico: mesmo apos o evento
        # motion.scan_complete, o comando seguinte as vezes nao era ACKado -
        # o servo do radar provavelmente ainda esta assentando de volta.
        # pequena folga extra aqui e mais barata que investigar o firmware
        # agora; revisitar se continuar acontecendo.
        await asyncio.sleep(PAUSA_APOS_SCAN_S)
        return leituras

    async def _ao_atualizar_estado_hardware(self, evento: Evento) -> None:
        estado = evento.dados.get("estado")
        if estado:
            self._estado_hardware = estado

    async def _aguardar_movimento_terminar(self) -> None:
        """Espera o movimento recem-mandado COMECAR e depois TERMINAR.

        `_aguardar_ocioso` sozinho nao basta logo apos um envio: o corpo ainda
        nao saiu de IDLE e a espera volta na hora. Aqui primeiro se espera o
        estado sair de IDLE (ate ESPERA_INICIO_MOVIMENTO_S - um giro ou avanco
        muito curto pode acabar antes de ser visto, e tudo bem), depois que
        ele volte.
        """
        esperado = 0.0
        while self._estado_hardware != "EXECUTING_MISSION" and esperado < ESPERA_INICIO_MOVIMENTO_S:
            await asyncio.sleep(INTERVALO_CHECAGEM_OCIOSO_S)
            esperado += INTERVALO_CHECAGEM_OCIOSO_S
        await self._aguardar_ocioso()

    async def _aguardar_ocioso(self) -> None:
        """Espera o Hardware Core sair de EXECUTING_MISSION antes de mandar
        o proximo comando de movimento (ver INTERVALO_CHECAGEM_OCIOSO_S)."""
        tempo_esperado_s = 0.0
        while self._estado_hardware == "EXECUTING_MISSION" and tempo_esperado_s < TIMEOUT_AGUARDAR_OCIOSO_S:
            await asyncio.sleep(INTERVALO_CHECAGEM_OCIOSO_S)
            tempo_esperado_s += INTERVALO_CHECAGEM_OCIOSO_S
        if self._estado_hardware == "EXECUTING_MISSION":
            logger.warning(
                "Hardware Core continua EXECUTING_MISSION apos %.1fs - prosseguindo mesmo assim",
                TIMEOUT_AGUARDAR_OCIOSO_S,
            )

    def _obstaculo_na_varredura(self, leituras: list[dict[str, Any]]) -> bool:
        distancias_validas = [
            leitura["distancia_cm"]
            for leitura in leituras
            if leitura.get("angulo") in ANGULOS_FRONTAIS and leitura.get("valida")
        ]
        if not distancias_validas:
            return False  # sem leitura valida (ex.: sensor nao montado) - nao bloqueia sozinho
        return min(distancias_validas) < self._distancia_seguranca_frontal_cm

    async def _publicar_obstaculo(
        self, contexto: str, motivo: str = "obstaculo_frontal", distancia_cm: float | None = None
    ) -> None:
        """`motivo` separa "tem coisa na frente" de "nao sei o que tem".

        Os dois impedem avancar, e sao fatos diferentes. O proprio firmware ja
        faz essa distincao desde 28/07 (`safety_manager.h`: "obstaculo_frontal"
        contra "frente_desconhecida") justamente porque relatar duvida como
        obstaculo faz o diagnostico mentir - afirma um obstaculo que os
        sensores desmentem. Vai no payload do evento que ja existe, em vez de
        um topico novo: topico sem assinante e o defeito que mais se repete
        neste projeto.

        `distancia_cm` explicita o valor que bloqueou quando ele nao vem do
        ultrassom (ex.: setor do LiDAR LD06, que nao e a `distancia_frontal_cm`
        da telemetria).
        """
        payload: dict[str, Any] = {
            "contexto": contexto,
            "motivo": motivo,
            "distancia_frontal_cm": self._distancia_frontal_atual_cm,
        }
        if distancia_cm is not None:
            payload["distancia_frontal_cm"] = distancia_cm
        await self._event_bus.publish(
            "navigation.obstacle_avoided",
            payload,
            prioridade=Prioridade.ALTA,
        )

    def _varredura_confirma_frente_livre(self, leituras: list[dict[str, Any]]) -> bool:
        """True SO quando a varredura mediu de verdade e mediu espaco.

        Note a diferenca para `_obstaculo_na_varredura`, que responde a
        pergunta oposta e devolve False quando nao ha leitura nenhuma - ali
        isso e correto ("nao bloqueia sozinho"), aqui seria o mesmo fail-open
        que este bloco existe para fechar. Varredura sem uma unica leitura
        valida nao confirma coisa alguma.
        """
        distancias = [
            leitura["distancia_cm"]
            for leitura in leituras
            if leitura.get("angulo") in ANGULOS_FRONTAIS and leitura.get("valida")
        ]
        if not distancias:
            return False
        return min(distancias) >= self._distancia_seguranca_frontal_cm

    async def _pode_avancar(self, contexto: str) -> bool:
        """Decide se da para andar para a frente AGORA - olhando, se preciso.

        POR QUE ISTO PRECISOU EXISTIR (2026-07-30). O EDR-0027 tirou o freio
        do Mega ("o mega nao pode vetar, o mega tem informar"), e a decisao
        passou inteira para ca. So que `_obstaculo_a_frente()` responde False
        quando a distancia e None - ou seja, "nao sei" saia daqui como "pode
        andar". O comentario daquele metodo ja mandava consultar
        `frente_desconhecida()` antes de concluir qualquer coisa do False, e
        NENHUM chamador consultava: o metodo existia sem um unico uso no
        projeto inteiro.

        O encadeamento medido: obstaculo PERTO -> eco de ~2,3 ms cabe inteiro
        na cegueira de ate 124 ms do loop do Mega -> `frontal_sem_eco` ->
        distancia None -> "livre" -> avanca. A falha apontava para o lado
        errado exatamente na hora que importa.

        NAO E UM FREIO, E UMA MANOBRA - regra do dono, e o que o
        `grounding.py` ja promete a IA em voz alta: *"Se eu nao sei o que ha a
        minha frente, a resposta e OLHAR, e nao desistir de me mover."* Entao
        duvida nao vira recusa: vira varredura. Só depois de olhar e continuar
        sem saber e que o avanco e negado - e com o motivo dito, nunca em
        silencio.
        """
        if self._obstaculo_a_frente():
            await self._comm.send(DESTINO_HARDWARE, {"comando": "STOP"})
            await self._publicar_obstaculo(contexto)
            return False

        # LiDAR NAO BLOQUEIA MAIS (decisao do dono, 2026-08-20): "nada de
        # sistema bloqueante" - o lidar alimenta o costmap do Nav2, e e o Nav2
        # quem replaneja a rota. O STOP por lidar foi removido; o lidar vira
        # evento de informacao (quem consumir - dashboard, IA - segue sabendo
        # do obstaculo) sem frear o avanca. O piso continua sendo o ultrassom
        # frontal acima (colisao iminente) e a parada reativa do Mega.
        if self._obstaculo_no_lidar():
            await self._publicar_obstaculo(
                contexto,
                motivo="lidar_frontal",
                distancia_cm=self._menor_distancia_lidar_frontal(),
            )

        if not self.frente_desconhecida():
            return True  # medida de verdade, e ela mostra espaco

        # Sensor mudo ou ausente: nao ha o que olhar. Anda, e o log diz que
        # esta sem medida - nunca em silencio (foi um `logger.debug` que
        # escondeu a falha do SET_PAN_TILT nesta mesma montagem, e o sintoma
        # foi "nao funciona, e o log esta limpo").
        if self._frontal_sensor_ok is not True:
            logger.warning(
                "avancando em '%s' SEM medida frontal: o sensor %s",
                contexto,
                "nunca reportou nada" if self._frontal_sensor_ok is None else "nao respondeu",
            )
            return True

        # DUVIDA VIRA ESTUDO, NUNCA RECUSA (decisao do dono, 2026-07-30):
        # *"o robo precisa ter mais iniciativa e coragem, travas de seguranca
        # so impedem o robo de andar - retira as travas, temos os sensores
        # para realizar estudos de risco"*. Mesma linha do EDR-0027, agora
        # aplicada ao Pi: quem tem sensor MEDE e INFORMA; decidir e da camada
        # de cima, com o dado na mao.
        #
        # A varredura continua acontecendo, e e ela o "estudo de risco": olhar
        # antes de andar rende informacao real (7 angulos) onde antes havia
        # so um None. O que mudou e o destino dessa informacao - ela vira
        # evento e log, e nao um freio.
        logger.info("frente desconhecida em '%s' - olhando antes de avancar", contexto)
        leituras = await self._escanear_a_frente()

        if self._varredura_confirma_frente_livre(leituras):
            return True

        # A varredura devolve o servo a 90 graus, entao a telemetria seguinte
        # pode ter trazido uma medida boa entre o fim do scan e este ponto.
        if not self.frente_desconhecida() and not self._obstaculo_a_frente():
            return True

        # Olhou e continua sem saber. AVANCA MESMO ASSIM, declarando o risco.
        # O evento carrega `medida_confiavel: False` para que quem consome -
        # diario, IA, interface - saiba que este trecho foi andado as cegas.
        # Um robo que anda sem saber e uma escolha do dono; um robo que anda
        # sem saber E sem registrar seria um problema de engenharia.
        await self._event_bus.publish(
            "navigation.risco_assumido",
            {
                "contexto": contexto,
                "motivo": "frente_desconhecida",
                "medida_confiavel": False,
                "angulos_varridos": len(leituras),
            },
            prioridade=Prioridade.ALTA,
        )
        logger.warning(
            "avancando em '%s' sem medida confiavel: olhei (%d angulos) e nada refletiu",
            contexto,
            len(leituras),
        )
        return True

    # ---------- entrada de missao (Cap 12 s.1: recebe missoes do Notebook) ----------

    async def _ao_receber_comando(self, evento: Evento) -> None:
        """Aceita o comando e VOLTA NA HORA - a execucao roda fora do bus.

        BUG REAL corrigido em 2026-07-28 (relato: "fica numa fila e isso e
        ruim"). Antes, este handler executava o comando inteiro aqui dentro, e
        `executar_manual` chama `_aguardar_ocioso()`, que espera
        `self._estado_hardware` sair de EXECUTING_MISSION.

        O laco de despacho do Event Bus e SERIAL: ele faz `await` no handler.
        Mas quem atualiza `_estado_hardware` e o handler de `motion.status`, do
        MESMO bus. Ou seja, a espera bloqueava a entrega do unico evento capaz
        de encerra-la: a condicao de saida era impossivel e `_aguardar_ocioso`
        SEMPRE queimava o timeout inteiro de 10s. Havia 86 dessas no log.

        Nesses 10s o barramento inteiro parava - nenhum evento de nenhum modulo
        era entregue - e os comandos de joystick se acumulavam para executar
        atrasados depois, que e o pior comportamento possivel num controle
        manual.

        O ULTIMO VENCE, e nao ha fila: comando de joystick velho nao deve ser
        executado atrasado, deve ser DESCARTADO. Soltar o joystick manda STOP,
        que por ser o mais recente ganha de qualquer movimento pendente. Uma
        fila faria o robo continuar andando depois de solto - exatamente o que
        a re, sem seguranca reativa, nao pode fazer (Cap 18).
        """
        # O DESCARTE DEIXA DE SER MUDO (2026-07-30).
        #
        # "O ultimo vence" continua valendo, e sem fila: comando velho
        # executado atrasado e o pior comportamento possivel num controle
        # manual (ver a docstring acima). O defeito nunca foi DESCARTAR - foi
        # descartar SEM DIZER.
        #
        # Custou um dia de diagnostico. A IA escolheu `explorar` 15 vezes e o
        # robo nao saiu do lugar: cada EXPLORE ficava nesta vaga e era
        # sobrescrito pelo GIRAR da `VigilanciaObstaculo` - que vive em
        # prioridade 100 e preempta a IA em 40 - antes de
        # `_executar_pendentes` chegar nele. Nenhuma linha em nenhuma das duas
        # pontas: a decisao da IA simplesmente evaporava.
        anterior = self._comando_pendente
        if anterior is not None:
            # QUAL descarte merece alarme (ajustado em 2026-07-30, medido).
            #
            # A primeira versao gritava em WARNING para TODO descarte, e o log
            # juntou 223 em minutos. Quase todos eram MANUAL apagando MANUAL - o
            # joystick reenvia a cada 300 ms (dashboard.js) e o "ultimo vence" e
            # exatamente o que deve acontecer ali. Alarme no caso normal nao
            # informa: ele afoga o caso raro que importa.
            #
            # O que importa e uma decisao AUTONOMA (EXPLORE, GOTO, PATROL) sendo
            # apagada antes de rodar - foi assim que 5444 decisoes da IA
            # evaporaram sem deixar rastro. Esse continua em WARNING; a rotina
            # do joystick desce para DEBUG, onde da para ler quando se procura.
            acao_anterior = anterior.get("acao")
            # MANUAL (joystick) e VELOCIDADE (Nav2, 10 Hz) sao fluxos: o novo
            # sempre substitui o velho, e avisar cada um so encheria o log.
            registrar = (
                logger.debug if acao_anterior in ("MANUAL", "VELOCIDADE") else logger.warning
            )
            registrar(
                "navegacao: comando %r DESCARTADO sem executar - %r chegou antes "
                "(o ultimo vence, nao ha fila)",
                acao_anterior,
                evento.dados.get("acao"),
            )
        self._comando_pendente = dict(evento.dados)
        if self._tarefa_comando is None or self._tarefa_comando.done():
            self._tarefa_comando = asyncio.create_task(self._executar_pendentes())

    async def _executar_pendentes(self) -> None:
        """Executa o comando pendente ate nao sobrar nenhum. Fora do bus."""
        while self._comando_pendente is not None:
            dados = self._comando_pendente
            self._comando_pendente = None
            await self._despachar_comando(dados)

    async def _despachar_comando(self, dados: dict[str, Any]) -> None:
        acao = dados.get("acao")
        try:
            if acao == "HOLD":
                await self.definir_modo_hold()
            elif acao == "MANUAL":
                await self.executar_manual(
                    dados["comando"],
                    dados.get("velocidade_percent"),
                    dados.get("distancia_cm"),
                )
            elif acao == "VELOCIDADE":
                await self.definir_velocidade(dados["v_m_s"], dados["w_rad_s"])
            elif acao == "GIRAR":
                await self.girar(
                    graus=dados["graus"],
                    sentido=dados["sentido"],
                    velocidade_percent=dados.get("velocidade_percent"),
                )
            elif acao == "DESVIAR":
                await self.desviar(lado=dados["lado"], mm=float(dados["mm"]))
            elif acao == "GOTO":
                await self.executar_goto(
                    graus=dados.get("graus", 0.0),
                    distancia_cm=dados.get("distancia_cm", 0.0),
                    velocidade_percent=dados.get("velocidade_percent"),
                )
            elif acao == "PATROL":
                await self.executar_patrol(dados["rota"])
            elif acao == "FOLLOW":
                await self.iniciar_follow()
            elif acao == "EXPLORE":
                await self.executar_explore()
            elif acao == "SCAN_FRONT":
                # Varredura avulsa (sem andar): a Ronda e o comando de voz
                # "varredura" pedem isto. O motion.scan_complete resultante
                # alimenta o mapa da web. Antes caia no else e virava erro.
                await self._escanear_a_frente()
            else:
                raise ErroNavegacao(f"acao de navegacao desconhecida: {acao!r}")
        except (ErroNavegacao, ErroComunicacao, KeyError) as erro:
            logger.warning("Falha ao processar navigation.comando %r: %s", dados, erro)
            await self._event_bus.publish(
                "navigation.error", {"acao": acao, "motivo": str(erro)}, prioridade=Prioridade.ALTA
            )

    async def _mudar_modo(self, novo_modo: ModoNavegacao) -> None:
        if novo_modo == self._modo:
            return
        # Sair de FOLLOW tem que MATAR o vigia de perda de alvo por QUALQUER
        # caminho, nao so pelo HOLD. Ate 2026-07-28 so o definir_modo_hold
        # cancelava, e isso passava despercebido porque nenhum outro modo era
        # alcancavel pela interface. Com o seletor de modo o vazamento fica a
        # um toque: quem esta em FOLLOW e pega o joystick vira MANUAL, mas o
        # vigia continua vivo e, passado o follow_lost_timeout_s sem deteccao,
        # manda STOP e forca HOLD - o robo morre na mao do operador, e nada na
        # tela explica por que.
        #
        # O `is not tarefa_atual` existe porque o proprio vigia chama isto ao
        # perder o alvo: cancelar a si mesmo levantaria CancelledError dentro
        # do publish abaixo e o navigation.mode_changed nunca sairia.
        if self._modo == ModoNavegacao.FOLLOW and self._tarefa_follow is not None:
            if self._tarefa_follow is not asyncio.current_task():
                self._tarefa_follow.cancel()
                self._tarefa_follow = None
        self._modo = novo_modo
        await self._event_bus.publish("navigation.mode_changed", {"modo": novo_modo.value})

    # ---------- HOLD ----------

    async def definir_modo_hold(self) -> None:
        """Para o robo e mantem so os sensores ativos (Cap 12 s.3)."""
        if self._tarefa_follow is not None:
            self._tarefa_follow.cancel()
            self._tarefa_follow = None
        # O modo muda ANTES do envio: o estado interno da navegacao nao pode
        # depender de o Arduino estar plugado. Antes, se o send falhasse, o
        # operador mandava PARAR, nao via erro nenhum, e o robo continuava
        # registrado em FOLLOW/PATROL.
        await self._mudar_modo(ModoNavegacao.HOLD)
        await self._comm.send(DESTINO_HARDWARE, {"comando": "STOP"})

    # ---------- MANUAL ----------

    async def executar_manual(
        self,
        comando: str,
        velocidade_percent: float | None = None,
        distancia_cm: float | None = None,
    ) -> None:
        """Repassa um comando direto do usuario ao Hardware Core (Cap 12 s.3).

        MANUAL OBEDECE 100% (decisao do dono, 2026-08-18): comando do operador
        nunca e vetado por sensor. Ate 18/08 a checagem tatica (`_pode_avancar`)
        engolia o avanco com um STOP quando havia obstaculo frontal; agora o
        obstaculo vira evento de informacao e aviso no log - o comando passa.
        O piso continua sendo a parada reativa do Mega (EDR-0027).
        """
        if comando not in COMANDOS_MANUAIS:
            raise ErroNavegacao(f"comando manual desconhecido: {comando!r}")

        await self._mudar_modo(ModoNavegacao.MANUAL)

        if comando in COMANDOS_QUE_AVANCAM:
            if self._obstaculo_a_frente():
                # Informa sem vetar: o evento de obstaculo continua indo para
                # o dashboard e a IA, e o operador segue dirigindo.
                await self._publicar_obstaculo(f"manual:{comando}")
                logger.warning(
                    "manual:%s OBEDECIDO apesar de obstaculo frontal a %.0f cm "
                    "(seguranca em %.0f cm)",
                    comando,
                    self._distancia_frontal_atual_cm,
                    self._distancia_seguranca_frontal_cm,
                )
            elif self._obstaculo_no_lidar():
                await self._publicar_obstaculo(
                    f"manual:{comando}",
                    motivo="lidar_frontal",
                    distancia_cm=self._menor_distancia_lidar_frontal(),
                )
                logger.warning(
                    "manual:%s OBEDECIDO apesar de obstaculo no lidar frontal",
                    comando,
                )

        payload: dict[str, Any] = {"comando": comando}
        comando_de_movimento = comando in COMANDOS_DE_MOVIMENTO
        if comando_de_movimento:
            payload["velocidade_percent"] = velocidade_percent or self._velocidade_padrao
            await self._aguardar_ocioso()  # nao empilha comando de movimento sobre outro em andamento
        if comando == "MOVE_DISTANCE" and distancia_cm is not None:
            payload["distancia_cm"] = distancia_cm
        await self._comm.send(DESTINO_HARDWARE, payload)

    # ---------- GIRAR (primitiva de manobra) ----------

    async def definir_velocidade(self, v_m_s: float, w_rad_s: float) -> None:
        """Velocidade continua do Nav2 -> CMD_VEL no firmware (2026-10-31).

        NAO espera o robo ficar ocioso, ao contrario do `executar_manual`: a
        10 Hz cada mensagem e a continuacao da anterior, e esperar faria o
        robo parar entre elas. Quem para e o proprio firmware, se as
        mensagens pararem de chegar por 0,5 s.
        """
        await self._mudar_modo(ModoNavegacao.MANUAL)
        await self._comm.send(
            DESTINO_HARDWARE,
            {"comando": "CMD_VEL", "v_m_s": float(v_m_s), "w_rad_s": float(w_rad_s)},
        )

    async def girar(
        self, graus: float, sentido: str, velocidade_percent: float | None = None
    ) -> None:
        """Gira o robo no proprio eixo, um angulo finito.

        POR QUE ISTO NAO E `MANUAL` COM TURN_LEFT/TURN_RIGHT, que ja existe:
        `ControleManual` (prioridade 95) assina `navigation.comando` e assume
        o controle do maestro em QUALQUER comando com `acao: "MANUAL"` - foi
        feito assim para o joystick do dashboard. Se o desvio de obstaculo
        usasse esse verbo, o maestro concluiria que um operador humano pegou o
        joystick: ao liberar a vigilancia, o `ControleManual` seguraria o robo
        por mais 1,5s achando que alguem esta dirigindo, e a autonomia ficaria
        presa atras de um operador que nao existe.

        `GIRAR` e uma manobra da NAVEGACAO, nao do operador. Tambem nao mexe
        no modo (`self._modo`) de proposito: desviar de um obstaculo acontece
        DENTRO do modo que estiver valendo (PATROL, EXPLORE...), e nao deve
        derrubar a missao em andamento.

        Nao ha checagem de obstaculo frontal aqui, e isso e correto: girar no
        proprio eixo nao aproxima o robo de nada. A camada reativa do Mega
        continua sendo o piso - ela permite o giro apenas quando o unico
        motivo da parada e a frente (safety_manager.h, `giroPermitido`).
        """
        comando = SENTIDOS_DE_GIRO.get(str(sentido).upper())
        if comando is None:
            raise ErroNavegacao(
                f"sentido de giro desconhecido: {sentido!r} "
                f"(esperado um de {sorted(SENTIDOS_DE_GIRO)})"
            )
        await self._aguardar_ocioso()
        await self._comm.send(
            DESTINO_HARDWARE,
            {
                "comando": comando,
                "graus": abs(float(graus)),
                "velocidade_percent": velocidade_percent or self._velocidade_padrao,
            },
        )

    # ---------- GOTO / segmentos (base tambem usada pelo PATROL) ----------

    async def desviar(self, lado: str, mm: float) -> bool:
        """Desloca o robo `mm` milimetros para o lado e volta ao rumo (2026-11-01).

        Robo diferencial nao anda de lado: gira 90 graus para o lado, anda o
        deslocamento e gira 90 de volta. A IA escolhe o lado e o quanto (o
        retrato da o desvio minimo em mm); aqui so se cumpre. O trecho reto e
        um GOTO, entao obstaculo no meio encerra o trecho e devolve a decisao.
        """
        lado = str(lado).upper()
        if lado not in SENTIDOS_DE_GIRO:
            raise ErroNavegacao(f"lado de desvio desconhecido: {lado!r}")
        volta = "DIREITA" if lado == "ESQUERDA" else "ESQUERDA"
        await self.girar(90.0, lado)
        completou = await self.executar_goto(graus=0.0, distancia_cm=abs(mm) / 10.0)
        await self.girar(90.0, volta)
        await self._event_bus.publish(
            "navigation.desvio", {"lado": lado, "mm": abs(mm), "completou": completou}
        )
        return completou

    async def executar_goto(
        self, graus: float, distancia_cm: float, velocidade_percent: float | None = None
    ) -> bool:
        """Um unico segmento planejado: opcionalmente gira, escaneia a frente
        (Cap 12 s.4: "antes de cada segmento: SCAN_FRONT") e anda a distancia
        pedida - abortando se houver obstaculo. Retorna True se completou."""
        await self._mudar_modo(ModoNavegacao.GOTO)
        await self._event_bus.publish(
            "navigation.plan_created", {"graus": graus, "distancia_cm": distancia_cm}
        )
        return await self._executar_segmento(graus, distancia_cm, velocidade_percent)

    async def _executar_segmento(
        self, graus: float, distancia_cm: float, velocidade_percent: float | None
    ) -> bool:
        velocidade = velocidade_percent or self._velocidade_padrao
        await self._event_bus.publish(
            "navigation.segment_started", {"graus": graus, "distancia_cm": distancia_cm}
        )

        # `_aguardar_ocioso` ja queimou o timeout inteiro de 10s em producao
        # antes (86 vezes num log so, pelo impasse do bus corrigido em 28/07).
        # Medir aqui o que ele custou impede que isso volte sem ninguem ver.
        _inicio_espera = time.monotonic()
        await self._aguardar_ocioso()  # segmento anterior (ou giro) pode ainda estar em execucao
        _espera_s = time.monotonic() - _inicio_espera
        if _espera_s > 1.0:
            logger.warning(
                "segmento: esperei %.1fs o Mega ficar ocioso (estado=%s)",
                _espera_s,
                self._estado_hardware,
            )

        if graus:
            comando_giro = "TURN_RIGHT" if graus > 0 else "TURN_LEFT"
            await self._comm.send(
                DESTINO_HARDWARE,
                {"comando": comando_giro, "graus": abs(graus), "velocidade_percent": velocidade},
            )
            await self._aguardar_ocioso()  # so escaneia/anda depois do giro terminar de verdade

        confirmado_pela_varredura = False
        if self._scan_antes_de_segmento:
            leituras = await self._escanear_a_frente()
            if self._obstaculo_na_varredura(leituras):
                distancias_validas = [
                    leitura["distancia_cm"]
                    for leitura in leituras
                    if leitura.get("angulo") in ANGULOS_FRONTAIS and leitura.get("valida")
                ]
                await self._comm.send(DESTINO_HARDWARE, {"comando": "STOP"})
                await self._publicar_obstaculo(
                    contexto="segmento",
                    motivo="obstaculo_frontal",
                    distancia_cm=min(distancias_validas),
                )
                return False
            confirmado_pela_varredura = self._varredura_confirma_frente_livre(leituras)

        # Varredura que MEDIU espaco ja e a olhada que `_pode_avancar` faria -
        # nao se paga 2,1s de scan duas vezes seguidas. Caso contrario (nada
        # medido, ou `patrol_scan_before_segment` desligado na config) vale a
        # checagem completa, que trata o "nao sei" em vez de o tomar por
        # "livre".
        if not confirmado_pela_varredura and not await self._pode_avancar("segmento"):
            return False

        logger.info(
            "segmento: mandando MOVE_DISTANCE %.0f cm a %s%%", distancia_cm, velocidade
        )
        await self._comm.send(
            DESTINO_HARDWARE,
            {"comando": "MOVE_DISTANCE", "distancia_cm": distancia_cm, "velocidade_percent": velocidade},
        )
        # "COMPLETADO" PASSA A SIGNIFICAR COMPLETADO (2026-08-04).
        #
        # Este evento saia logo apos o `send`, e o `send` so garante o ACK - que
        # diz que o comando CHEGOU, nao que o robo andou. O firmware executa
        # MOVE_DISTANCE de forma assincrona (ver INTERVALO_CHECAGEM_OCIOSO_S),
        # entao `segment_completed` era publicado com o robo ainda parado, no
        # instante em que o movimento MAL COMECOU.
        #
        # Quem consome pela palavra - diario, interface, quem for medir trecho
        # percorrido - lia um segmento pronto que ainda nao existia. Um evento
        # que mente e pior que um evento que falta: o segundo se percebe.
        #
        # Nao custa tempo novo: o proximo passo do EXPLORE ja esperava aqui de
        # qualquer jeito (`_escanear_a_frente` espera ficar ocioso desde hoje),
        # e o PATROL ja esperava no inicio do segmento seguinte. A espera so
        # mudou de lugar - e agora o evento sai na hora certa.
        await self._aguardar_ocioso()
        await self._event_bus.publish(
            "navigation.segment_completed", {"graus": graus, "distancia_cm": distancia_cm}
        )
        return True

    # ---------- PATROL ----------

    async def executar_patrol(self, rota: list[dict[str, Any]]) -> None:
        """Percorre uma rota (lista de segmentos), com nova tentativa
        limitada por segmento quando um obstaculo interrompe (Cap 12 s.4).

        Ciclo fechado (Camada 6): depois de UMA falha de avanco, o degrau
        pergunta "a mesma situacao ja falhou varias vezes na janela?" - e se
        sim, NAO repete o erro: aborta a rota e publica
        `navigation.obstacle_recorrente` (o relato para o dono) em vez de
        continuar batendo de frente. O limite por segmento (`obstacle_retry_max`)
        continua cuidando da rajada de tentativas em segundos; este degrau
        cuida do episodio que se repete ao longo dos minutos.
        """
        await self._mudar_modo(ModoNavegacao.PATROL)
        for segmento in rota:
            tentativas = 0
            concluido = False
            repeticoes = 0
            while not concluido and tentativas < self._tentativas_obstaculo_max:
                concluido = await self._executar_segmento(
                    segmento.get("graus", 0.0),
                    segmento.get("distancia_cm", 0.0),
                    segmento.get("velocidade_percent"),
                )
                tentativas += 1
                if not concluido:
                    repeticoes = await self._avancar_repetidamente_bloqueado()
                if not concluido and repeticoes >= self._recorrente_limite:
                    logger.warning(
                        "patrulha abortada: avancar ja falhou %d vezes na janela de %.0fs - nao vou repetir o erro",
                        repeticoes,
                        self._recorrente_janela_s,
                    )
                    await self._event_bus.publish(
                        "navigation.obstacle_recorrente",
                        {
                            "situacao": "avancar",
                            "repeticoes": repeticoes,
                            "janela_s": self._recorrente_janela_s,
                            "limite": self._recorrente_limite,
                        },
                    )
                    return
            if not concluido:
                logger.warning(
                    "Segmento de patrulha abortado apos %d tentativas: %s", tentativas, segmento
                )
                return
        await self._mudar_modo(ModoNavegacao.HOLD)

    async def _avancar_repetidamente_bloqueado(self) -> int:
        """Quantas vezes a situacao de avancar ja falhou na janela.

        O degrau do ciclo fechado: em vez de repetir o erro de frente, o robo
        para e reporta. Falha da consulta (memoria fora, banco travado) devolve
        0 - o comportamento segue o padrao (repetir), porque preferir avancar
        com duvida de memoria e melhor do que parar por um banco ausente.
        Consulta nao injetada = degrau desligado (0).
        """
        if self._consultar_repeticoes is None:
            return 0
        try:
            return int(await self._consultar_repeticoes("avancar", self._recorrente_janela_s) or 0)
        except Exception:
            logger.exception(
                "falha ao consultar repeticoes de avanco - seguindo o comportamento padrao"
            )
            return 0

    # ---------- FOLLOW (versao minima - ver docstring do modulo) ----------

    async def iniciar_follow(self) -> None:
        """Versao minima do Cap 12 s.5: fica em modo FOLLOW reagindo a
        vision.person_recognized (publicado pelo Vision Core no Notebook,
        so quando o alvo bate com alguem cadastrado) via
        _ao_reconhecer_pessoa. Escolha deliberada (2026-07-28): o robo NAO
        anda atras de qualquer pessoa, so de quem ele reconhece - antes disso
        reagia a vision.person_detected (qualquer pessoa), que alem de nao
        ser o comportamento desejado nunca tinha "centro_x" no payload real
        (so "caixa"), entao o giro estava morto em producao mesmo com o modo
        FOLLOW ativo. O calculo de correcao proporcional completo e a
        rotacao de busca apos perda (s.5 passo 5) ficam para quando o Vision
        Core estiver de fato integrado ponta-a-ponta com este modulo - por
        ora so o timeout de perda esta implementado."""
        await self._mudar_modo(ModoNavegacao.FOLLOW)
        self._ultima_deteccao_follow_s = time.monotonic()
        if self._tarefa_follow is not None:
            self._tarefa_follow.cancel()
        self._tarefa_follow = asyncio.create_task(self._monitorar_perda_de_alvo())

    async def _ao_reconhecer_pessoa(self, evento: Evento) -> None:
        if self._modo != ModoNavegacao.FOLLOW:
            return
        self._ultima_deteccao_follow_s = time.monotonic()
        centro_x = evento.dados.get("centro_x", 0.5)  # 0..1, 0.5 = centralizado
        desvio = centro_x - 0.5
        if abs(desvio) < 0.1:
            return  # alvo ja centralizado o suficiente - nao gira a toa
        comando = "TURN_RIGHT" if desvio > 0 else "TURN_LEFT"
        await self._comm.send(
            DESTINO_HARDWARE,
            {"comando": comando, "velocidade_percent": self._velocidade_reduzida_perto_pessoa},
        )

    async def _monitorar_perda_de_alvo(self) -> None:
        # checa em fracoes do timeout configurado (nunca mais de 1s) - com
        # timeout fixo de 1s essa checagem chegaria atrasada demais para
        # timeouts curtos (ex.: em teste).
        intervalo_checagem_s = min(1.0, self._follow_lost_timeout_s / 3)
        try:
            while True:
                await asyncio.sleep(intervalo_checagem_s)
                if time.monotonic() - self._ultima_deteccao_follow_s > self._follow_lost_timeout_s:
                    # Achado real da vistoria de 2026-07-24: esta task roda
                    # solta (asyncio.create_task em iniciar_follow), fora do
                    # unico lugar que tratava ErroComunicacao
                    # (_ao_receber_comando). Se a serial com o Arduino
                    # falhasse bem aqui, a excecao subia sem tratamento e
                    # matava a task silenciosamente - o modo ficava travado
                    # em FOLLOW para sempre e navigation.target_lost nunca
                    # era publicado. Agora tratamos a falha de envio: o
                    # Arduino tem seu proprio timeout reativo de comando
                    # (Cap 18 - para sozinho sem novo comando em 5s) que
                    # cobre o STOP nao ter chegado, mas o estado do
                    # NavigationCore precisa sair de FOLLOW de qualquer
                    # jeito, senao nada aqui tenta de novo.
                    try:
                        await self._comm.send(DESTINO_HARDWARE, {"comando": "STOP"})
                        await self._comm.send(DESTINO_HARDWARE, {"comando": "SCAN_FRONT"})
                    except Exception:
                        # `except Exception` de proposito, e nao so
                        # ErroComunicacao: sair de FOLLOW e mais importante que
                        # qualquer falha ao avisar o Arduino. Ficar preso em
                        # FOLLOW e pior do que qualquer erro que apareca aqui -
                        # o robo volta a girar atras da primeira pessoa que
                        # aparecer, sem ter feito o STOP.
                        logger.exception(
                            "Falha ao enviar STOP/SCAN_FRONT ao perder o alvo do FOLLOW - "
                            "saindo do modo mesmo assim (o Arduino tem seu proprio timeout "
                            "reativo de comando)"
                        )
                    finally:
                        # No `finally` para que a saida do modo aconteca mesmo se
                        # o publish ou o proprio except falharem.
                        await self._event_bus.publish("navigation.target_lost", {})
                        await self._mudar_modo(ModoNavegacao.HOLD)
                    return
        except asyncio.CancelledError:
            pass

    # ---------- EXPLORE (versao minima - ver docstring do modulo) ----------

    async def executar_explore(self) -> None:
        """Explora um trecho DESVIANDO sozinho (2026-11-01).

        Cada passo olha o LiDAR que ja esta na memoria e decide (ver
        `desvio_local.escolher_passo`): avanca o que cabe, gira para a
        direcao mais aberta, ou - so quando nenhuma direcao tem espaco -
        encerra e avisa a IA. A IA escolhe EXPLORAR; o desvio e daqui.

        Ate esta data o trecho andava reto 30 cm por passo sem conferir se
        cabiam 30 cm, e chamava o radar de servo que saiu do robo
        (`SCAN_FRONT`): cada passo esperava 3 s por uma resposta que nunca
        vinha. Com 25,7 cm livres ele mandou 30 e bateu na comoda (log de
        01/11 16:20); a IA mandou explorar de novo e ele bateu na parede.
        """
        logger.info("explore: comecando - desvio local pelo LiDAR")
        await self._mudar_modo(ModoNavegacao.EXPLORE)

        for passo in range(1, SEGMENTOS_POR_EXPLORE + 1):
            # O TRECHO E INTERROMPIVEL A CADA PASSO (2026-07-30): STOP,
            # joystick ou HOLD que chegaram na vaga encerram o trecho aqui.
            if self._comando_pendente is not None:
                logger.info(
                    "explore: comando %r chegou - encerrando o trecho no passo %d",
                    self._comando_pendente.get("acao"),
                    passo,
                )
                return

            idade = (
                None
                if self._raios_recebido_em is None
                else time.monotonic() - self._raios_recebido_em
            )
            if idade is None or idade > IDADE_MAXIMA_VARREDURA_S:
                logger.warning(
                    "explore: sem varredura recente do LiDAR (idade %s) - nao ando as cegas",
                    "nenhuma" if idade is None else f"{idade:.1f}s",
                )
                await self._publicar_obstaculo(contexto="explore", motivo="frente_desconhecida")
                return

            decisao = escolher_passo(
                self._raios_lidar,
                limite_cm=self._limite_desvio_cm,
                passo_max_cm=DISTANCIA_SEGMENTO_EXPLORE_CM,
                meia_largura_cm=self._meia_largura_desvio_cm,
            )
            logger.info(
                "explore: passo %d/%d - %s %s (%s)",
                passo,
                SEGMENTOS_POR_EXPLORE,
                decisao.acao,
                "" if decisao.valor is None else f"{decisao.valor:.0f}",
                decisao.motivo,
            )

            if decisao.acao == AVANCAR:
                if not await self._avancar_explore(decisao.valor):
                    return
            elif decisao.acao == GIRAR:
                sentido = "DIREITA" if decisao.valor > 0 else "ESQUERDA"
                await self.girar(abs(decisao.valor), sentido)
                await self._aguardar_movimento_terminar()
                await asyncio.sleep(ESPERA_VARREDURA_APOS_GIRO_S)
            else:
                await self._publicar_obstaculo(
                    contexto="explore",
                    motivo="obstaculo_frontal",
                    distancia_cm=decisao.livre_cm,
                )
                return

        logger.info("explore: trecho concluido - devolvendo a decisao a IA")

    async def _avancar_explore(self, distancia_cm: float) -> bool:
        """Um avanco do EXPLORE, ja medido pelo desvio local.

        Nao passa por `_executar_segmento` de proposito: aquele caminho chama
        o `SCAN_FRONT` (radar que nao existe mais, 3 s de espera por passo) e
        refaz a checagem de frente com outro limiar. A distancia aqui ja saiu
        do LiDAR, e a trava do LiDAR no enlace confere de novo e para no meio
        se algo aparecer.
        """
        await self._aguardar_ocioso()
        await self._event_bus.publish(
            "navigation.segment_started", {"graus": 0.0, "distancia_cm": distancia_cm}
        )
        logger.info(
            "explore: mandando MOVE_DISTANCE %.0f cm a %s%%", distancia_cm, self._velocidade_padrao
        )
        try:
            await self._comm.send(
                DESTINO_HARDWARE,
                {
                    "comando": "MOVE_DISTANCE",
                    "distancia_cm": distancia_cm,
                    "velocidade_percent": self._velocidade_padrao,
                },
            )
        except ErroComunicacao as erro:
            # Inclui a recusa da trava do LiDAR: obstaculo surgiu entre a
            # decisao e o envio. Nao insiste - devolve a decisao.
            logger.warning("explore: avanco recusado/nao entregue: %s", erro)
            return False
        await self._aguardar_movimento_terminar()
        await self._event_bus.publish(
            "navigation.segment_completed", {"graus": 0.0, "distancia_cm": distancia_cm}
        )
        return True
