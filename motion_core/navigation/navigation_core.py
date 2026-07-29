"""Motion Core / Navegação (Cap 12) — roda no Raspberry Pi.

Principio (Cap 12 s.2): o Notebook decide (missao), o Raspberry navega
(este modulo), o Arduino so executa comandos simples e devolve telemetria.
Este modulo nunca fala com o Arduino direto por transporte - so por meio
do ComunicacaoService (comm.send/request), igual qualquer outro modulo
(Cap 14 s.7).

Sem hardware fisico montado ainda (motores/sensores - ver docs/journal.md)
- toda a logica daqui e validada em nivel de protocolo, contra
`tools/sim_arduino.py` ou um FakeTransporte de teste, nunca com movimento
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
from orion.kernel.event_bus import Evento, EventBus, Prioridade

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
        # referencia dos proprios sensores.
        "CALIBRATE_IMU",
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
    ) -> None:
        self._event_bus = event_bus
        self._comm = comm
        self._velocidade_padrao = config_motion["max_speed_percent"]
        self._distancia_seguranca_frontal_cm = config_motion["min_front_distance_cm"]
        self._timeout_comando_s = config_motion["mission_timeout_s"]
        self._scan_antes_de_segmento = config_navigation["patrol_scan_before_segment"]
        self._tentativas_obstaculo_max = config_navigation["obstacle_retry_max"]
        self._follow_lost_timeout_s = config_navigation["follow_lost_timeout_s"]
        self._velocidade_reduzida_perto_pessoa = config_navigation[
            "reduced_speed_near_person_percent"
        ]

        self._modo = ModoNavegacao.HOLD
        self._distancia_frontal_atual_cm: float | None = None
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

    @property
    def modo(self) -> ModoNavegacao:
        return self._modo

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

        # "NAO MEDI" NAO E "ESTA LIVRE" (2026-07-29).
        #
        # Quando nada reflete, o firmware reporta o TETO DE ALCANCE (517 cm)
        # com `distancia_frontal_valida: true` - ver _foraDeAlcance em
        # sensor_ultrassonico.h. O proprio driver marca `frontal_sem_eco` para
        # denunciar que aquilo nao e medicao, e ate 2026-07-29 ninguem olhava:
        # nem a seguranca do Mega, nem aqui.
        #
        # Por que isso e perigoso e nao so impreciso: o eco de um obstaculo a
        # 40 cm dura ~2,3 ms, e o loop do Mega fica cego por ate 124 ms
        # (medido em 29/07, ~88 ms so no envio da telemetria). O eco inteiro
        # cabe dentro da cegueira, o pino ECHO nunca e visto subir, e o driver
        # conclui "nada refletiu". Ou seja: quanto MAIS PERTO o obstaculo,
        # maior a chance de ele ser reportado como caminho livre. Falha para o
        # lado errado, e justamente na hora que importa.
        #
        # Aqui isso vira None - "nao sei" - e quem decide o que fazer com a
        # duvida e a camada de cima, com a camera e o radar, em vez de receber
        # uma parede imaginaria a 5 metros.
        if payload.get("frontal_sem_eco"):
            self._distancia_frontal_atual_cm = None
            return
        distancia = payload.get("distancia_frontal_cm")
        if distancia is not None and payload.get("distancia_frontal_valida", True):
            self._distancia_frontal_atual_cm = distancia

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

    async def _ao_completar_scan(self, evento: Evento) -> None:
        if self._scan_pendente is not None and not self._scan_pendente.done():
            self._scan_pendente.set_result(evento.dados.get("leituras", []))

    async def _escanear_a_frente(self) -> list[dict[str, Any]]:
        """Manda SCAN_FRONT e espera o motion.scan_complete de verdade (nao
        so o ACK, que so confirma que a varredura comecou - ver TIMEOUT_SCAN_S).
        Em timeout, segue com lista vazia (a checagem por telemetria continua
        valendo como rede de seguranca)."""
        loop = asyncio.get_running_loop()
        self._scan_pendente = loop.create_future()
        await self._comm.send(DESTINO_HARDWARE, {"comando": "SCAN_FRONT"})
        try:
            leituras = await asyncio.wait_for(self._scan_pendente, timeout=TIMEOUT_SCAN_S)
        except asyncio.TimeoutError:
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

    async def _publicar_obstaculo(self, contexto: str) -> None:
        await self._event_bus.publish(
            "navigation.obstacle_avoided",
            {"contexto": contexto, "distancia_frontal_cm": self._distancia_frontal_atual_cm},
            prioridade=Prioridade.ALTA,
        )

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
            elif acao == "GIRAR":
                await self.girar(
                    graus=dados["graus"],
                    sentido=dados["sentido"],
                    velocidade_percent=dados.get("velocidade_percent"),
                )
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
        """Repassa um comando direto do usuario ao Hardware Core (Cap 12 s.3),
        com a checagem de obstaculo da camada tatica antes de qualquer
        comando que avance o robo (Cap 12 s.6)."""
        if comando not in COMANDOS_MANUAIS:
            raise ErroNavegacao(f"comando manual desconhecido: {comando!r}")

        await self._mudar_modo(ModoNavegacao.MANUAL)

        if comando in COMANDOS_QUE_AVANCAM and self._obstaculo_a_frente():
            await self._comm.send(DESTINO_HARDWARE, {"comando": "STOP"})
            await self._publicar_obstaculo(contexto=f"manual:{comando}")
            return

        payload: dict[str, Any] = {"comando": comando}
        comando_de_movimento = comando in COMANDOS_DE_MOVIMENTO
        if comando_de_movimento:
            payload["velocidade_percent"] = velocidade_percent or self._velocidade_padrao
            await self._aguardar_ocioso()  # nao empilha comando de movimento sobre outro em andamento
        if comando == "MOVE_DISTANCE" and distancia_cm is not None:
            payload["distancia_cm"] = distancia_cm
        await self._comm.send(DESTINO_HARDWARE, payload)

    # ---------- GIRAR (primitiva de manobra) ----------

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

        await self._aguardar_ocioso()  # segmento anterior (ou giro) pode ainda estar em execucao

        if graus:
            comando_giro = "TURN_RIGHT" if graus > 0 else "TURN_LEFT"
            await self._comm.send(
                DESTINO_HARDWARE,
                {"comando": comando_giro, "graus": abs(graus), "velocidade_percent": velocidade},
            )
            await self._aguardar_ocioso()  # so escaneia/anda depois do giro terminar de verdade

        obstaculo_na_varredura = False
        if self._scan_antes_de_segmento:
            leituras = await self._escanear_a_frente()
            obstaculo_na_varredura = self._obstaculo_na_varredura(leituras)

        if obstaculo_na_varredura or self._obstaculo_a_frente():
            await self._comm.send(DESTINO_HARDWARE, {"comando": "STOP"})
            await self._publicar_obstaculo(contexto="segmento")
            return False

        await self._comm.send(
            DESTINO_HARDWARE,
            {"comando": "MOVE_DISTANCE", "distancia_cm": distancia_cm, "velocidade_percent": velocidade},
        )
        await self._event_bus.publish(
            "navigation.segment_completed", {"graus": graus, "distancia_cm": distancia_cm}
        )
        return True

    # ---------- PATROL ----------

    async def executar_patrol(self, rota: list[dict[str, Any]]) -> None:
        """Percorre uma rota (lista de segmentos), com nova tentativa
        limitada por segmento quando um obstaculo interrompe (Cap 12 s.4)."""
        await self._mudar_modo(ModoNavegacao.PATROL)
        for segmento in rota:
            tentativas = 0
            concluido = False
            while not concluido and tentativas < self._tentativas_obstaculo_max:
                concluido = await self._executar_segmento(
                    segmento.get("graus", 0.0),
                    segmento.get("distancia_cm", 0.0),
                    segmento.get("velocidade_percent"),
                )
                tentativas += 1
            if not concluido:
                logger.warning(
                    "Segmento de patrulha abortado apos %d tentativas: %s", tentativas, segmento
                )
                return
        await self._mudar_modo(ModoNavegacao.HOLD)

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
        """Versao minima do Cap 12 s.3: escaneia a frente e anda um segmento
        curto se estiver livre. Nao ainda um algoritmo real de exploracao de
        area (mapeamento incremental) - isso fica para o SLAM previsto no
        Cap 12 s.12 (ORION OS 2.0+) ou uma iteracao futura desta fase."""
        await self._mudar_modo(ModoNavegacao.EXPLORE)
        leituras = await self._escanear_a_frente()
        if self._obstaculo_na_varredura(leituras) or self._obstaculo_a_frente():
            await self._publicar_obstaculo(contexto="explore")
            return
        await self._executar_segmento(graus=0.0, distancia_cm=30.0, velocidade_percent=None)
