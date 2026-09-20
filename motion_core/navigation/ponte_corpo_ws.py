"""Transporte WebSocket para o corpo - faz o ESP32 novo parecer o antigo.

POR QUE EXISTE (2026-09-20). O ESP32 do corpo foi regravado com um firmware
que conversa por WiFi/WebSocket, nao pela serial binaria que o Motion Core
sempre usou. O sintoma era `Hardware Core nao encontrado em nenhuma porta` a
cada 35 s, com toda a telemetria de hardware nula - o corpo nao estava mudo,
estava falando outra lingua.

O FONTE DESSE FIRMWARE NAO EXISTE (procurado no Pi e no Windows). O protocolo
abaixo foi extraido da FLASH do proprio chip - backup em
~/backups/firmware_corpo/corpo_websocket_2026-09-20.bin, strings no offset
~1376867 - e o `ping` confirmado ao vivo:

    {"cmd":"ping"}  ->  {"pong":true}                 (confirmado 2026-09-20)

Vocabulario COMPLETO do firmware, como saiu do binario:

    {"cmd":"drive","left":V,"right":V}   tracao diferencial, CONTINUA
    {"cmd":"stop"}
    {"cmd":"gimbal","pan":V,"tilt":V}
    {"cmd":"ping"} | {"cmd":"gimbal_test"}

E a telemetria que ele emite sozinho, ~2 Hz:

    {"tof":[-1,-1,-1,-1],"obstacle_distance":-1,"passos_esq":0,
     "passos_dir":0,"rssi":-61,"pan_us":1500,"tilt_us":1500}

--------------------------------------------------------------------------
POR QUE UM **TRANSPORTE**, E NAO UM MODULO QUE ESCUTA O BARRAMENTO

A primeira versao disto assinava um evento `motion.comando_hardware` e
publicava `hardware.telemetry`. Os dois nomes existiam SO AQUI: ninguem
publicava o primeiro e ninguem consumia o segundo. Seria o mesmo "publicar no
vazio" que ja mordeu este projeto varias vezes - a ponte pareceria pronta e
nunca receberia um comando.

Quem manda comando ao corpo e o `ComunicacaoService` (`comm.send("hardware_core",
...)`), chamado pelo NavigationCore; e quem consome a telemetria sao cinco
lugares que assinam `comm.mensagem.telemetry` (estado_do_mundo, instintos,
navigation_core, fusao_sensores, webui).

Entao esta classe implementa a interface `Transporte` e e registrada como o
link "hardware_core". Do lado de dentro do Motion Core NADA muda - o
NavigationCore, o SafetyManager e a webui continuam falando exatamente o que
falavam com a serial. E o mesmo principio que o motor_manager.h usou quando
os steppers viraram motor DC: troca-se o que acontece por dentro, nao a API.

--------------------------------------------------------------------------
AS TRES COISAS QUE ESTE FIRMWARE NAO FAZ (e que a ponte remenda ou declara)

1. NAO MANDA ACK. O ComunicacaoService espera ACK e retransmite 3x sem ele -
   e o que enche o log de "ACK nao recebido". A ponte FORJA o ACK depois de
   entregar o comando ao firmware. O ACK passa a significar "entreguei", e
   nao mais "o corpo confirmou" - que e menos garantia do que a serial dava.

2. NAO ANDA DISTANCIA. So aceita velocidade continua: `drive` anda ate alguem
   mandar `stop`. Para "ande 50 cm" alguem tem de contar pulsos e frear na
   hora certa - e aqui esse alguem fica no Pi, com o WiFi no meio da malha,
   exatamente o que o EDR-0031 desaconselha. Trate a precisao como GROSSEIRA.
   O lugar certo dessa conta e dentro do ESP32.

3. NAO TEM BUSSOLA. A telemetria antiga trazia `rumo_graus` e
   `bussola_calibrada`; esta nao traz. Quem depende de rumo (giro por angulo,
   pose) fica sem essa fonte ate o firmware voltar a publicar.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from orion.communication.protocol import Mensagem, TipoMensagem

logger = logging.getLogger("motion_core.navigation.ponte_corpo_ws")

#: Reconexao com backoff, igual ao resto do projeto: link que cai tem de
#: voltar sozinho, sem ninguem reiniciar servico.
RECONEXAO_ESPERA_INICIAL_S = 1.0
RECONEXAO_ESPERA_MAXIMA_S = 30.0

#: Sem telemetria por este tempo, o corpo esta perdido mesmo com o socket de
#: pe. O firmware emite a ~2 Hz; 6 s sao doze silencios seguidos.
TIMEOUT_TELEMETRIA_S = 6.0

#: Escala de `left`/`right`. NAO CONFIRMADA - o binario nao diz e descobrir
#: exige girar roda. 255 e o tipico de um PWM de 8 bits em codigo Arduino.
#: MEDIR NA BANCADA, COM AS RODAS NO AR, antes de qualquer movimento no chao.
ESCALA_DRIVE = 255.0

#: Quantos pulsos antes do alvo mandar o `stop`. O comando atravessa o WiFi e
#: o robo nao para no mesmo instante; parar cedo erra menos que parar tarde,
#: porque bater custa mais que ficar curto. MEDIR na bancada.
MARGEM_PARADA_PASSOS = 0

#: Teto do angulo mandado ao gimbal, em modulo.
#:
#: POR QUE EXISTE, E POR QUE E APERTADO: a UNIDADE que o firmware espera em
#: `{"cmd":"gimbal","pan":V}` NAO E CONHECIDA - o fonte nao existe e o binario
#: nao diz. O Motion Core fala GRAUS (`pan_graus`), mas a telemetria devolve
#: MICROSSEGUNDOS (`pan_us":1500`), entao as duas leituras sao plausiveis.
#:
#: Mandar a unidade errada joga o servo no batente, e servo em estol ja
#: derrubou ESTE ESP32 antes (puxa >1A de uma USB de 100 mA - ver o historico
#: do projeto). Limitar a +-90 nao elimina o risco, mas impede o caso pior:
#: um valor em microssegundos (1000-2000) chegar inteiro num firmware que le
#: graus e mandar o servo para o infinito.
#:
#: PRIMEIRO TESTE DE GIMBAL: cabeca livre, sem nada travando o curso, e olho
#: no `pan_us` da telemetria para descobrir a unidade de verdade.
ANGULO_MAXIMO_GIMBAL = 90.0


def _limitar_angulo(valor: Any) -> float:
    try:
        graus = float(valor)
    except (TypeError, ValueError):
        return 0.0
    return max(-ANGULO_MAXIMO_GIMBAL, min(ANGULO_MAXIMO_GIMBAL, graus))


class TransporteCorpoWS:
    """Implementa `orion.communication.transport.Transporte` sobre WebSocket.

    Nao decide nada: quem decide continua sendo NavigationCore/BehaviorCore.
    Aqui so se traduz vocabulario e se cuida do link.
    """

    def __init__(
        self,
        url: str,
        pulsos_por_metro: float,
        pulsos_por_grau: float,
        nome_local: str = "hardware_core",
        nome_remoto: str = "motion_core",
    ) -> None:
        self._url = url
        self._pulsos_por_metro = pulsos_por_metro
        self._pulsos_por_grau = pulsos_por_grau
        self._nome_local = nome_local
        self._nome_remoto = nome_remoto

        self._ws: Any = None
        self._conectado = False
        #: quadros prontos para o ComunicacaoService consumir (ACKs forjados
        #: e telemetria traduzida)
        self._saida: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

        self._telemetria: dict[str, Any] = {}
        self._ultima_telemetria_s: float | None = None

        #: movimento finito em curso (ver limitacao 2 no cabecalho)
        self._alvo_passos: int | None = None
        self._passos_inicio: tuple[int, int] | None = None
        self._em_movimento = False

    # ---------- interface Transporte ----------

    @property
    def conectado(self) -> bool:
        return self._conectado

    async def conectar(self) -> None:
        """Sobe o laco de conexao e so volta quando houver telemetria.

        Esperar a PRIMEIRA telemetria, e nao so o socket abrir, e o que
        impede o Motion Core de declarar o corpo presente para um firmware
        travado com o TCP de pe.
        """
        asyncio.create_task(self._laco())
        limite = asyncio.get_running_loop().time() + 10.0
        while asyncio.get_running_loop().time() < limite:
            if self._ultima_telemetria_s is not None:
                return
            await asyncio.sleep(0.2)
        if not self._conectado:
            raise ConnectionError(f"corpo nao respondeu em {self._url}")

    async def enviar(self, payload: bytes) -> None:
        """Recebe um quadro do protocolo ORION e traduz para o firmware."""
        try:
            mensagem = Mensagem.from_bytes(payload)
        except Exception:
            logger.warning("quadro invalido para o corpo (%d bytes)", len(payload))
            return

        if mensagem.tipo is TipoMensagem.HEARTBEAT:
            # O firmware nao conhece heartbeat. A telemetria continua a ~2 Hz
            # ja prova que ele esta vivo, entao responder aqui nao e mentira.
            await self._responder(TipoMensagem.HEARTBEAT, {}, mensagem.id)
            return

        if mensagem.tipo is not TipoMensagem.COMMAND:
            return

        entregue = await self._traduzir_e_enviar(mensagem.payload or {})
        # ACK/NACK forjado - ver limitacao 1 no cabecalho.
        await self._responder(
            TipoMensagem.ACK if entregue else TipoMensagem.NACK,
            {"ok": entregue},
            mensagem.id,
        )

    async def receber(self) -> AsyncIterator[bytes]:
        while True:
            yield await self._saida.get()

    async def fechar(self) -> None:
        """Freia o robo ANTES de soltar o link: o firmware nao para sozinho."""
        if self._conectado:
            await self._enviar_bruto({"cmd": "stop"})
        self._conectado = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("falha ao fechar o socket do corpo", exc_info=True)
            self._ws = None

    # ---------- link ----------

    async def _laco(self) -> None:
        espera = RECONEXAO_ESPERA_INICIAL_S
        while True:
            try:
                await self._conectar_e_escutar()
                espera = RECONEXAO_ESPERA_INICIAL_S
            except asyncio.CancelledError:
                raise
            except Exception as erro:
                logger.warning("corpo (%s) fora: %s", self._url, erro)
            self._conectado = False
            await asyncio.sleep(espera)
            espera = min(espera * 2, RECONEXAO_ESPERA_MAXIMA_S)

    async def _conectar_e_escutar(self) -> None:
        async with aiohttp.ClientSession() as sessao:
            async with sessao.ws_connect(self._url, timeout=10, heartbeat=5) as ws:
                self._ws = ws
                self._conectado = True
                logger.info("corpo conectado em %s", self._url)
                async for msg in ws:
                    if msg.type is aiohttp.WSMsgType.TEXT:
                        await self._ao_receber(msg.data)
        self._ws = None

    # ---------- o que chega do corpo ----------

    async def _ao_receber(self, texto: str) -> None:
        try:
            dados = json.loads(texto)
        except ValueError:
            return
        if "passos_esq" not in dados:
            return  # pong ou outra coisa que nao e telemetria

        self._telemetria = dados
        self._ultima_telemetria_s = asyncio.get_running_loop().time()
        await self._conferir_movimento_finito(dados)
        await self._emitir(TipoMensagem.TELEMETRY, self._traduzir_telemetria(dados))

    def _traduzir_telemetria(self, dados: dict[str, Any]) -> dict[str, Any]:
        """Do JSON do firmware para os campos que o Motion Core le.

        Os nomes NAO batem (`passos_esq` la, `passos_esquerda` aqui) e os
        consumidores sao cinco - traduzir num lugar so e o que evita cada um
        aprender o dialeto novo.
        """
        distancia = dados.get("obstacle_distance", -1)
        return {
            "estado": "MOVING" if self._em_movimento else "IDLE",
            "em_movimento": self._em_movimento,
            "passos_esquerda": dados.get("passos_esq", 0),
            "passos_direita": dados.get("passos_dir", 0),
            # -1 e o "nao sei" do firmware. Vira None e nao 0: zero seria
            # "obstaculo colado", a leitura mais perigosa que existe.
            "distancia_frontal_cm": None if distancia in (-1, None) else distancia,
            "frontal_sensor_ok": distancia not in (-1, None),
            "pan_us": dados.get("pan_us"),
            "tilt_us": dados.get("tilt_us"),
            "rssi": dados.get("rssi"),
            # rumo_graus/bussola_calibrada NAO entram: este firmware nao tem
            # bussola, e inventar um rumo e pior que nao ter (ver cabecalho).
        }

    async def _conferir_movimento_finito(self, dados: dict[str, Any]) -> None:
        if self._alvo_passos is None or self._passos_inicio is None:
            return
        try:
            esq = int(dados["passos_esq"])
            dir_ = int(dados["passos_dir"])
        except (KeyError, TypeError, ValueError):
            return

        andou = max(abs(esq - self._passos_inicio[0]), abs(dir_ - self._passos_inicio[1]))
        if andou < max(0, self._alvo_passos - MARGEM_PARADA_PASSOS):
            return

        await self._enviar_bruto({"cmd": "stop"})
        self._alvo_passos = None
        self._passos_inicio = None
        self._em_movimento = False
        # O NavigationCore espera este evento para saber que o robo CHEGOU -
        # sem ele, `_aguardar_ocioso` esperaria o timeout a cada segmento.
        await self._emitir(
            TipoMensagem.EVENT, {"topico": "motion.movimento_concluido", "passos": andou}
        )

    # ---------- o que vai para o corpo ----------

    async def _emitir(self, tipo: TipoMensagem, payload: dict[str, Any]) -> None:
        mensagem = Mensagem.nova(tipo, self._nome_local, self._nome_remoto, payload)
        try:
            self._saida.put_nowait(mensagem.to_bytes())
        except asyncio.QueueFull:
            # Fila cheia = ninguem consumindo. Derrubar a telemetria NOVA
            # seria guardar a velha; aqui o dado recente vale mais.
            logger.warning("fila de recepcao do corpo cheia - quadro descartado")

    async def _responder(
        self, tipo: TipoMensagem, payload: dict[str, Any], id_referencia: str
    ) -> None:
        mensagem = Mensagem.nova(
            tipo, self._nome_local, self._nome_remoto, payload, id_referencia=id_referencia
        )
        try:
            self._saida.put_nowait(mensagem.to_bytes())
        except asyncio.QueueFull:
            logger.warning("fila cheia - %s nao entregue", tipo.value)

    async def _enviar_bruto(self, payload: dict[str, Any]) -> bool:
        if self._ws is None or not self._conectado:
            logger.warning("corpo offline - %s descartado", payload.get("cmd"))
            return False
        try:
            await self._ws.send_str(json.dumps(payload))
            return True
        except Exception:
            logger.exception("falha ao enviar %s ao corpo", payload.get("cmd"))
            return False

    async def _traduzir_e_enviar(self, payload: dict[str, Any]) -> bool:
        comando = str(payload.get("comando", "")).upper()
        velocidade = float(payload.get("velocidade_percent") or 0) / 100.0

        if comando == "STOP":
            self._alvo_passos = None
            self._em_movimento = False
            return await self._enviar_bruto({"cmd": "stop"})

        if comando in ("SET_PAN_TILT", "SET_PAN", "SET_TILT", "GIMBAL", "PAN_TILT"):
            gimbal: dict[str, Any] = {"cmd": "gimbal"}
            # O Motion Core fala `pan_graus`/`tilt_graus`; o firmware aceita
            # `pan`/`tilt` - ver o aviso sobre UNIDADE no topo do modulo.
            for origem, destino in (("pan_graus", "pan"), ("tilt_graus", "tilt"),
                                    ("pan", "pan"), ("tilt", "tilt")):
                if origem in payload and destino not in gimbal:
                    gimbal[destino] = _limitar_angulo(payload[origem])
            if len(gimbal) == 1:
                return False  # nenhum eixo informado: nao manda comando vazio
            return await self._enviar_bruto(gimbal)

        if comando == "SCAN_FRONT":
            # O radar de ultrassom saiu do robo em 19/09 e este firmware nao
            # tem nada equivalente. Responder "sem leitura" e melhor que
            # deixar o NavigationCore esperando os 2,1 s do timeout a cada
            # segmento de ronda.
            await self._emitir(
                TipoMensagem.EVENT, {"topico": "motion.scan_complete", "leituras": []}
            )
            return True

        alvo = self._traduzir_movimento(comando, payload, velocidade)
        if alvo is None:
            logger.warning("comando %r nao tem traducao para este firmware", comando)
            return False

        esquerda, direita, passos = alvo
        if passos is not None:
            self._passos_inicio = (
                int(self._telemetria.get("passos_esq", 0)),
                int(self._telemetria.get("passos_dir", 0)),
            )
            self._alvo_passos = passos
        self._em_movimento = True
        return await self._enviar_bruto(
            {
                "cmd": "drive",
                "left": round(esquerda * ESCALA_DRIVE),
                "right": round(direita * ESCALA_DRIVE),
            }
        )

    def _traduzir_movimento(
        self, comando: str, payload: dict[str, Any], velocidade: float
    ) -> tuple[float, float, int | None] | None:
        """(esquerda, direita, passos_alvo) - passos None = movimento continuo."""
        if velocidade <= 0:
            velocidade = 0.5

        if comando in ("MOVE_FORWARD", "MOVE_CONTINUOUS"):
            return velocidade, velocidade, None
        if comando == "MOVE_BACK":
            return -velocidade, -velocidade, None
        if comando == "MOVE_DISTANCE":
            cm = float(payload.get("distancia_cm") or 0)
            passos = int(abs(cm) / 100.0 * self._pulsos_por_metro)
            sinal = 1.0 if cm >= 0 else -1.0
            return sinal * velocidade, sinal * velocidade, passos
        if comando in ("TURN_LEFT", "TURN_RIGHT"):
            graus = float(payload.get("graus") or 0)
            passos = int(abs(graus) * self._pulsos_por_grau) if graus else None
            if comando == "TURN_LEFT":
                return -velocidade, velocidade, passos
            return velocidade, -velocidade, passos
        return None

    # ---------- saude ----------

    def esta_saudavel(self) -> bool:
        """Socket aberto NAO basta: o firmware pode travar com o TCP de pe.
        Quem prova que ha alguem vivo do outro lado e a telemetria."""
        if not self._conectado or self._ultima_telemetria_s is None:
            return False
        silencio = asyncio.get_running_loop().time() - self._ultima_telemetria_s
        return silencio < TIMEOUT_TELEMETRIA_S
