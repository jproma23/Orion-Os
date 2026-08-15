"""Fusao de Sensores (Cap 12 secao 8) - roda no Raspberry Pi.

A cada pacote TELEMETRY recebido do Hardware Core (Arduino), este modulo:
1. Estima a pose 2D do robo (x, y, orientacao) e a velocidade real por
   odometria diferencial (encoders/passos dos motores), publicando o
   resultado como `motion.position`.
2. Usa a IMU (MPU6050) para detectar inclinacao perigosa e impacto, e
   publica um evento critico de seguranca quando isso acontece.

Este e um modulo separado do `NavigationCore` (que ja assina
`comm.mensagem.telemetry` para a checagem tatica de obstaculo frontal) -
"um modulo = uma responsabilidade" (ARQUITETURA.txt regra #9): odometria/IMU e
uma responsabilidade distinta de planejamento de movimento. Os dois
assinam o mesmo topico de telemetria de forma independente pelo Event Bus,
sem se conhecerem (regra #1).

## Decisao de escopo: so odometria diferencial, sem fusao de rumo com a IMU

O Cap 12 s.8 fala em combinar "orientacao... da MPU6050" na fusao. Na
telemetria de hoje (`firmware/hardware_core/include/telemetry_manager.h`)
a IMU so expoe `inclinacao_graus` (um angulo de inclinacao do chassi,
util pra detectar tombamento) e `impacto_detectado` (bool) - NAO ha
yaw/heading nem giroscopio bruto disponivel no pacote. Ou seja, nao existe
hoje um dado de "para onde o robo esta apontando" vindo da IMU pra corrigir
o rumo calculado pelos encoders.

Por isso esta primeira versao calcula (x, y, orientacao) por odometria
diferencial classica pura (delta de passos da roda esquerda/direita ->
distancia percorrida por cada roda -> distancia do centro + rotacao),
usando `motion.steps_per_meter` e `motion.odometry_correction_factor`
(Cap 12 s.9, autocalibracao) do `config/orion.yaml`. `inclinacao_graus` e
`impacto_detectado` sao usados SO para a deteccao de seguranca (item 2
acima), nunca para corrigir x/y/orientacao. Mesmo padrao de "minimo
viavel + gap documentado" usado em FOLLOW/EXPLORE no `navigation_core.py`.
Se o firmware um dia expuser yaw integrado do giroscopio, a fusao de
verdade (encoder + IMU) pode ser adicionada aqui sem mudar o topico
`motion.position` nem quem o consome.

## Sem hardware fisico montado ainda

Nenhum motor/encoder esta fisicamente montado nesta fase (ver
docs/journal.md) - os `passos_esquerda`/`passos_direita` reais valem 0 ou
lixo. Toda a logica de odometria aqui e validada com telemetria sintetica
em teste (`tests/unit/test_fusao_sensores.py`), nunca com deslocamento
real. A logica de seguranca da IMU, por sua vez, PODE ser validada com o
Mega real (ele ja tem MPU6050 conectada de verdade - ver
`docs/project_orion_os_wiring.md`/memoria do projeto).
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

from orion.kernel.event_bus import Evento, EventBus, Prioridade

logger = logging.getLogger("motion_core.navigation.fusao_sensores")

#: DE ONDE SAIU A POSE - e se ela pode ser chamada de MEDIDA (2026-07-30).
#:
#: Pedido do dono, ao ver a interface afirmar 85 cm de deslocamento e 80 graus
#: de giro com os motores FISICAMENTE DESCONECTADOS dos drivers:
#:
#:   *"nao pode haver deslocamento de medidas e nem de bussola nem de mpu nem
#:   de webui"*, e *"se a confirmando esta sendo pelo envio de pulsos vai haver
#:   essa mentira na webui, devemos usar dados reais captados pelo sensor para
#:   altera a webui"*.
#:
#: Ele esta certo, e o proprio firmware confessa a origem do numero:
#:
#:     long passosAcumulados = 0;  // odometria simples (sem encoder)
#:     roda.passosAcumulados += roda.sentidoFrente ? 1 : -1;   // motor_manager.h
#:
#: `passos_esquerda` / `passos_direita` contam cada pulso que o firmware ENVIA
#: ao TB6600. E comando, nao medicao: com as rodas suspensas, com o rele aberto
#: ou sem motor nenhum ligado, os passos correm e a pose "anda". Integrar isso e
#: dead reckoning sem realimentacao - nao existe nada no laco que possa
#: desmentir o comando.
#:
#: Por isso a pose passa a viajar DECLARANDO a origem. Quem exibe decide o que
#: fazer com um numero nao medido; hoje a webui deixa de mostra-lo como fato.
ORIGEM_PASSOS_COMANDADOS = "passos_comandados"
#: Pulso lido na roda - deslocamento que de fato aconteceu.
ORIGEM_ENCODER = "encoder"

#: Campos que o firmware precisaria mandar para a pose virar MEDIDA.
#:
#: O `EncoderManager` JA existe no firmware, JA e iniciado (`main.cpp:333`) e JA
#: conta pulso real por interrupcao nos pinos 18/19 - e o `telemetry_manager.h`
#: manda os campos do MotorManager, nao os dele. O dado certo e coletado e
#: ninguem le, o padrao que mais se repete neste projeto.
#:
#: Falta, para valer: (1) a telemetria carregar estes campos; (2) os sensores
#: opticos instalados - CONFERIR tensao e se a saida e NPN ou PNP, porque PNP em
#: 12V queima o pino do Mega; (3) calibrar pulso->metro, que depende do disco
#: usado. Enquanto (3) nao for MEDIDO na bancada, converter pulso em metro seria
#: afirmar antes de medir - trocar uma mentira por outra.
#: ATUALIZADO 2026-08-02: os campos existem. O firmware do ESP32 passou a
#: mandar estes nomes no pacote REDUZIDO (o que sai com o robo em movimento,
#: que e o unico momento em que odometria existe).
#:
#: Os nomes NAO foram escolhidos agora - eles ja estavam fixados aqui e em
#: `test_pose_com_pulso_de_roda_se_declara_medida`, escritos justamente "para
#: o campo nao nascer errado" no dia da gravacao. O firmware e que foi
#: ajustado para obedecer. Foi o teste que pegou a divergencia.
#:
#: Dos tres itens que faltavam acima: (1) esta FEITO; (2) o dono definiu a
#: montagem - sensor indutivo NPN com 6 chapas por roda, e NPN e seguro no
#: pino de 3,3V porque so puxa para o GND; (3) continua PENDENTE e e o que
#: separa "a roda girou" de "o robo andou 40 cm" - ver
#: `encoder_pulsos_por_metro` no __init__.
CAMPOS_ENCODER = ("pulsos_esquerdo", "pulsos_direito")


class FusaoSensores:
    """Combina odometria (encoders) e seguranca da IMU a cada telemetria."""

    def __init__(self, event_bus: EventBus, config_motion: dict[str, Any]) -> None:
        self._event_bus = event_bus
        self._steps_per_meter: float = config_motion["steps_per_meter"]
        self._fator_correcao: float = config_motion["odometry_correction_factor"]
        self._wheel_base_m: float = config_motion["wheel_base_m"]
        self._limite_inclinacao_graus: float = config_motion["tilt_limit_degrees"]
        #: Quantos pulsos de encoder valem UM METRO percorrido pela roda.
        #:
        #: `None` = ainda nao medido, e esse e o valor honesto ate alguem
        #: empurrar o robo um metro com fita metrica e ler o contador. Nao ha
        #: como deduzir isto de catalogo: depende do diametro real da roda com
        #: o peso do robo em cima, e a conta teorica erra justamente no que a
        #: odometria mais sente.
        #:
        #: Enquanto for None, os pulsos NAO viram distancia - mas nao sao
        #: inuteis: eles ainda dizem se a roda GIROU, e e isso que desmente a
        #: pose quando ela afirma deslocamento com o motor parado. Medir
        #: converte "a roda girou" em "o robo andou 40 cm".
        #:
        #: Ordem de grandeza para conferir o resultado: com 6 chapas por volta
        #: e roda de ~6,5 cm, da ~29 pulsos/m, ou seja **3,4 cm por pulso**.
        #: E grosso de proposito - resolve a mentira, nao entrega precisao de
        #: mapeamento. Numero muito diferente disso significa chapa contada a
        #: mais (ruido) ou a menos.
        self._encoder_pulsos_por_metro = config_motion.get("encoder_pulses_per_meter")
        #: True quando a leitura ATUAL foi um reinicio de placa - ver
        #: `_delta_encoder`. Existe para separar "nao ha encoder" de "o encoder
        #: acabou de resincronizar", que pedem respostas opostas na pose.
        self._encoder_resincronizou = False

        self._encoder_esquerda_anterior: int | None = None
        self._encoder_direita_anterior: int | None = None

        # pose acumulada (referencial: origem e orientacao 0 = onde o robo
        # estava quando este modulo foi criado / primeira telemetria chegou)
        self._x_m = 0.0
        self._y_m = 0.0
        self._orientacao_rad = 0.0

        # base para calcular o delta entre duas leituras de telemetria
        # consecutivas - None ate a primeira leitura chegar
        self._passos_esquerda_anterior: int | None = None
        self._passos_direita_anterior: int | None = None
        self._instante_anterior_s: float | None = None

        # evita publicar safety.safe_mode_entered repetidamente a cada
        # telemetria (chega a cada 500ms) enquanto o perigo persiste -
        # so publica na borda de subida/descida (Cap 18 s.9)
        self._safe_mode_ativo = False

        # None = ainda nao chegou telemetria nenhuma. Guarda o ultimo
        # `imu_conectado` visto para logar so na BORDA (conectou/desconectou),
        # nao a cada telemetria - mesmo motivo do `_safe_mode_ativo` acima.
        self._imu_conectada_anterior: bool | None = None

        event_bus.subscribe("comm.mensagem.telemetry", self._ao_receber_telemetria)

    @property
    def pose_atual(self) -> tuple[float, float, float]:
        """(x_m, y_m, orientacao_graus) - util em testes/diagnostico."""
        return (self._x_m, self._y_m, math.degrees(self._orientacao_rad))

    async def _ao_receber_telemetria(self, evento: Evento) -> None:
        # TELEMETRY nao passa pela normalizacao de topico das EVENT (Cap 14
        # s.7) - o dado real fica aninhado em "payload" (Mensagem.to_dict()),
        # mesmo detalhe ja documentado em NavigationCore._ao_atualizar_telemetria.
        payload = evento.dados.get("payload", {})
        await self._atualizar_odometria(payload)
        await self._checar_seguranca_imu(payload)

    # ---------- odometria diferencial (Cap 12 s.8: encoders/passos) ----------

    async def _atualizar_odometria(self, payload: dict[str, Any]) -> None:
        passos_esquerda = payload.get("passos_esquerda")
        passos_direita = payload.get("passos_direita")
        if passos_esquerda is None or passos_direita is None:
            return  # telemetria sem os campos de encoder - nada a fazer

        agora = time.monotonic()

        if self._passos_esquerda_anterior is None:
            # primeira leitura: so guarda a base, ainda nao da pra calcular
            # um delta (nao existe leitura anterior pra comparar)
            self._passos_esquerda_anterior = passos_esquerda
            self._passos_direita_anterior = passos_direita
            self._instante_anterior_s = agora
            # A BASE DO ENCODER TEM QUE SER GUARDADA AQUI TAMBEM.
            #
            # Este return acontece antes de `_delta_encoder` ser chamado, e se
            # a base dos pulsos so fosse tomada la, ela ficaria uma telemetria
            # ATRASADA em relacao a dos passos - o primeiro delta de passos
            # sairia com o encoder ainda sem referencia, e a pose comecaria
            # como "nao medida" mesmo com pulso chegando. Falha discreta: nada
            # quebra, so a primeira janela de movimento nao conta como medida.
            if self._tem_encoder(payload):
                self._encoder_esquerda_anterior = int(payload[CAMPOS_ENCODER[0]])
                self._encoder_direita_anterior = int(payload[CAMPOS_ENCODER[1]])
            return

        delta_esquerda = passos_esquerda - self._passos_esquerda_anterior
        delta_direita = passos_direita - self._passos_direita_anterior
        delta_tempo_s = agora - self._instante_anterior_s

        self._passos_esquerda_anterior = passos_esquerda
        self._passos_direita_anterior = passos_direita
        self._instante_anterior_s = agora

        # REINICIO DO MEGA: os dois contadores voltam a zero no mesmo quadro,
        # porque `passosAcumulados` e membro do MotorManager e nasce zerado.
        # Essa e a assinatura que se pode confiar - e nao "delta negativo",
        # ver o bloco seguinte.
        if (
            passos_esquerda == 0
            and passos_direita == 0
            and (delta_esquerda != 0 or delta_direita != 0)
        ):
            logger.warning(
                "Contadores de passo zeraram juntos (delta esq=%d dir=%d) - "
                "provavel reinicio do Mega, resincronizando sem atualizar pose",
                delta_esquerda,
                delta_direita,
            )
            return

        # DELTA NEGATIVO E RE LEGITIMA, nao erro.
        #
        # Ate 2026-07-27 este metodo descartava qualquer delta negativo como
        # "reinicio ou overflow" e voltava sem atualizar a pose. Mas o firmware
        # DECREMENTA de proposito quando anda para tras
        # (motor_manager.h: `passosAcumulados += sentidoFrente ? 1 : -1`), o que
        # significa que TODA marcha a re caia neste return: a pose congelava e o
        # log enchia de "Contagem de passos regrediu". Desencontro de suposicao
        # entre as duas camadas - o comentario antigo aqui afirmava que so podia
        # ser reinicio, e nunca foi verdade.
        #
        # O sinal do delta e justamente o que a odometria diferencial precisa: a
        # conta abaixo ja trata negativo corretamente (anda para tras em x/y e,
        # se so uma roda inverter, gira). Overflow do `long` de 32 bits nao e
        # preocupacao pratica: a 4000 passos/m daria mais de 500 km.

        if delta_tempo_s <= 0:
            return  # telemetria duplicada ou fora de ordem - evita divisao por zero

        # O QUE A RODA FEZ, ao lado do que foi COMANDADO (2026-08-02).
        #
        # Os dois deltas convivem de proposito. `delta_*` e comando; `pulso_*`
        # e medida. Guardar so o que vale mais jogaria fora justamente a
        # comparacao - e e a comparacao que desmente a pose. Foi a falta dela
        # que deixou a interface afirmar 85 cm e 80 graus com os motores
        # fisicamente desligados.
        self._encoder_resincronizou = False
        pulso_esquerda, pulso_direita = self._delta_encoder(
            payload, delta_esquerda, delta_direita
        )
        if self._encoder_resincronizou:
            # Placa reiniciada nesta amostra: a base do encoder acabou de ser
            # refeita e os passos comandados nao descrevem o intervalo. Pular a
            # integracao perde UM quadro de telemetria (~1 s) e mantem a pose
            # honesta - o proximo quadro ja volta ao normal.
            return
        comandou = delta_esquerda != 0 or delta_direita != 0
        girou = pulso_esquerda is not None and (pulso_esquerda != 0 or pulso_direita != 0)
        # None enquanto nao houver encoder na telemetria: "nao sei" nao pode
        # virar False, senao a interface passaria a acusar roda travada num
        # robo que so nao tem sensor instalado.
        rodas_confirmadas: bool | None = None
        if pulso_esquerda is not None:
            rodas_confirmadas = (not comandou) or girou
            if comandou and not girou:
                logger.warning(
                    "COMANDO SEM GIRO: passos esq=%+d dir=%+d, mas os encoders nao "
                    "contaram pulso nenhum. Roda travada, patinando, sem energia "
                    "(rele aberto) ou sensor desligado - a pose NAO avancou por conta disso.",
                    delta_esquerda,
                    delta_direita,
                )

        # Duas condicoes, nao uma: ter pulso NAO basta para virar medida. Sem
        # `encoder_pulsos_por_metro` medido, converter pulso em metro seria
        # inventar a escala - trocar uma mentira por outra, que e exatamente o
        # que este arquivo se recusa a fazer desde 30/07.
        usar_encoder = pulso_esquerda is not None and bool(self._encoder_pulsos_por_metro)
        if usar_encoder:
            # Pulso -> metro. Nao ha fator de correcao aqui de proposito: o
            # `odometry_correction_factor` existe para compensar o erro entre
            # o passo comandado e a roda real, e quando se mede a roda esse
            # erro simplesmente nao existe mais. Aplicar os dois seria corrigir
            # duas vezes.
            metros_por_pulso = 1.0 / self._encoder_pulsos_por_metro
            distancia_esquerda_m = pulso_esquerda * metros_por_pulso
            distancia_direita_m = pulso_direita * metros_por_pulso
        else:
            metros_por_passo = 1.0 / self._steps_per_meter
            distancia_esquerda_m = delta_esquerda * metros_por_passo * self._fator_correcao
            distancia_direita_m = delta_direita * metros_por_passo * self._fator_correcao
            # Comandou e a roda nao girou: com pulso disponivel, isto NAO pode
            # somar na pose. E o caso exato de 30/07, e nao adianta so avisar
            # no log - a interface le a pose, nao o log.
            if rodas_confirmadas is False:
                distancia_esquerda_m = 0.0
                distancia_direita_m = 0.0

        distancia_centro_m = (distancia_direita_m + distancia_esquerda_m) / 2.0
        # rotacao diferencial classica: roda direita andou mais -> robo gira
        # para a esquerda (orientacao aumenta, convencao matematica CCW).
        delta_orientacao_rad = (distancia_direita_m - distancia_esquerda_m) / self._wheel_base_m

        # integracao "ponto medio": projeta x/y usando a orientacao na METADE
        # do movimento (media entre a orientacao antes e depois do passo),
        # em vez de so a orientacao antiga (Euler simples) - erro menor
        # quando o robo gira e anda no mesmo intervalo de telemetria.
        orientacao_media_rad = self._orientacao_rad + delta_orientacao_rad / 2.0
        self._x_m += distancia_centro_m * math.cos(orientacao_media_rad)
        self._y_m += distancia_centro_m * math.sin(orientacao_media_rad)
        self._orientacao_rad = (self._orientacao_rad + delta_orientacao_rad) % (2 * math.pi)

        velocidade_m_s = distancia_centro_m / delta_tempo_s

        # A pose diz de onde veio. Sem encoder na telemetria ela e ESTIMATIVA
        # a partir do que foi comandado, e vai marcada como tal - quem exibe
        # nao tem como saber isso por conta propria, e foi assim que a interface
        # passou a afirmar 85 cm com os motores desligados.
        origem = ORIGEM_ENCODER if usar_encoder else ORIGEM_PASSOS_COMANDADOS
        await self._event_bus.publish(
            "motion.position",
            {
                "x_m": round(self._x_m, 4),
                "y_m": round(self._y_m, 4),
                "orientacao_graus": round(math.degrees(self._orientacao_rad), 2),
                "velocidade_m_s": round(velocidade_m_s, 4),
                "origem": origem,
                "medida": usar_encoder,
                # None = sem encoder na telemetria, "nao sei". True = mandou e
                # a roda girou (ou nao mandou nada). False = MANDOU E A RODA
                # NAO GIROU, e a pose foi segurada por causa disso. Os tres
                # casos sao diferentes e a interface precisa distinguir os
                # tres - foi juntar "nao sei" com "tudo bem" que produziu a
                # pose mentirosa.
                "rodas_confirmadas": rodas_confirmadas,
            },
        )

    def _delta_encoder(
        self,
        payload: dict[str, Any],
        delta_passos_esquerda: int,
        delta_passos_direita: int,
    ) -> tuple[int | None, int | None]:
        """Pulsos desde a telemetria anterior, ou (None, None) sem encoder.

        Guarda a base na primeira leitura, como a contagem de passos ja faz -
        um contador acumulado desde o boot da placa nao diz nada sozinho; o
        que a odometria consome e a diferenca entre duas leituras.
        """
        if not self._tem_encoder(payload):
            return (None, None)

        esquerda = int(payload[CAMPOS_ENCODER[0]])
        direita = int(payload[CAMPOS_ENCODER[1]])

        if self._encoder_esquerda_anterior is None:
            self._encoder_esquerda_anterior = esquerda
            self._encoder_direita_anterior = direita
            return (None, None)

        delta_e = esquerda - self._encoder_esquerda_anterior
        delta_d = direita - self._encoder_direita_anterior
        self._encoder_esquerda_anterior = esquerda
        self._encoder_direita_anterior = direita

        # O CONTADOR DE PULSOS NUNCA DIMINUI, e essa e a limitacao central
        # desta montagem: um sensor indutivo por roda, sem quadratura, conta
        # chapa passando e nao sabe para que lado. Delta negativo aqui so pode
        # ser reinicio da placa - resincroniza sem mexer na pose.
        if delta_e < 0 or delta_d < 0:
            logger.warning(
                "Contador de encoder regrediu (esq=%+d dir=%+d) - reinicio da "
                "placa, resincronizando sem atualizar pose",
                delta_e,
                delta_d,
            )
            # A PROMESSA DO LOG PASSA A SER VERDADE (2026-08-04).
            #
            # Ate hoje este `return (None, None)` era indistinguivel de "nao ha
            # encoder nesta montagem", e o chamador tratava os dois igual: caia
            # no ramo dos PASSOS COMANDADOS e somava a pose assim mesmo. Ou
            # seja, a linha acima dizia "sem atualizar pose" enquanto a pose era
            # atualizada - e pela via menos confiavel que existe aqui,
            # justamente na amostra em que a placa acabou de reiniciar e os
            # passos tambem nao valem nada.
            #
            # Era pequeno (so no reinicio) e era exatamente a doenca que este
            # arquivo existe para nao ter: a pose afirmando o que ninguem mediu.
            self._encoder_resincronizou = True
            return (None, None)

        # O SENTIDO VEM DO COMANDO, porque o sensor nao sabe dizer.
        #
        # Um sensor indutivo por roda conta chapa passando; ele nao distingue
        # frente de tras (isso exigiria quadratura, ou seja um segundo sensor
        # defasado por roda). Entao o SINAL vem de qual sentido foi comandado
        # naquela roda - que e o que `delta_passos_*` ja carrega, porque o
        # firmware decrementa `passosAcumulados` ao andar para tras.
        #
        # Isto NAO devolve a mentira pela janela: o comando decide apenas o
        # SINAL, e a MAGNITUDE - o quanto a roda de fato girou - continua
        # vindo do pulso. Com a roda travada, zero pulso e zero metro qualquer
        # que seja o sinal. O que se perde e saber que o robo foi EMPURRADO a
        # mao com o motor parado: os pulsos aparecem sem comando, e sem
        # sentido conhecido eles nao viram deslocamento.
        sinal_e = -1 if delta_passos_esquerda < 0 else 1
        sinal_d = -1 if delta_passos_direita < 0 else 1
        return (delta_e * sinal_e, delta_d * sinal_d)

    @staticmethod
    def _tem_encoder(payload: dict[str, Any]) -> bool:
        """True quando a telemetria trouxe pulso REAL de roda.

        Passou a devolver True em producao a partir de 2026-08-02: o firmware
        do ESP32 manda `CAMPOS_ENCODER` no pacote reduzido. Antes disso ficava
        sempre False - o `EncoderManager` contava pulso por interrupcao e a
        telemetria mandava os campos do MotorManager, nao os dele. O dado
        certo era coletado e ninguem lia.
        """
        return all(payload.get(campo) is not None for campo in CAMPOS_ENCODER)

    # ---------- seguranca da IMU (Cap 12 s.8 paragrafo 2; Cap 18) ----------

    async def _checar_seguranca_imu(self, payload: dict[str, Any]) -> None:
        imu_conectada = bool(payload.get("imu_conectado", False))
        if imu_conectada != self._imu_conectada_anterior:
            # Antes disto o desconectar da IMU nao deixava rastro nenhum no
            # log: o `return` abaixo saia calado, e "MPU6050 nao conectada"
            # so dava pra descobrir olhando a telemetria bruta ou perguntando
            # ao dono por que a seguranca de inclinacao/impacto parou de
            # funcionar. A borda (True->False e False->True) e logada aqui,
            # nao a cada telemetria (chega a cada 500ms) - mesmo motivo do
            # `_safe_mode_ativo`.
            if imu_conectada:
                logger.info("IMU (MPU6050) conectada - seguranca de inclinacao/impacto ativa")
            else:
                logger.warning(
                    "IMU (MPU6050) desconectada - sem deteccao de inclinacao/impacto ate reconectar"
                )
            self._imu_conectada_anterior = imu_conectada

        if not imu_conectada:
            return  # sem IMU conectada nesta leitura - nada a avaliar

        inclinacao_graus = payload.get("inclinacao_graus")
        impacto_detectado = bool(payload.get("impacto_detectado", False))
        # nao ha dado de giroscopio/tombamento separado na telemetria hoje
        # (ver docstring do modulo) - inclinacao acima do limite cobre tanto
        # "inclinacao perigosa" quanto o caso extremo de tombamento.
        inclinacao_perigosa = (
            inclinacao_graus is not None and abs(inclinacao_graus) >= self._limite_inclinacao_graus
        )

        perigo = inclinacao_perigosa or impacto_detectado

        if perigo and not self._safe_mode_ativo:
            motivo = "impacto_detectado" if impacto_detectado else "inclinacao_perigosa"
            self._safe_mode_ativo = True
            logger.warning(
                "Evento critico de seguranca via IMU: motivo=%s inclinacao_graus=%s impacto=%s",
                motivo,
                inclinacao_graus,
                impacto_detectado,
            )
            await self._event_bus.publish(
                "safety.safe_mode_entered",
                {
                    "motivo": motivo,
                    "inclinacao_graus": inclinacao_graus,
                    "impacto_detectado": impacto_detectado,
                },
                prioridade=Prioridade.CRITICA,
            )
        elif not perigo and self._safe_mode_ativo:
            self._safe_mode_ativo = False
            logger.info("Condicao de seguranca da IMU normalizada - saindo de SAFE_MODE (fusao)")
            await self._event_bus.publish(
                "safety.safe_mode_exited", {}, prioridade=Prioridade.ALTA
            )
