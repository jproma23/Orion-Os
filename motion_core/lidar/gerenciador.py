"""Gerenciador do LiDAR - roda no Raspberry Pi (Motion Core).

Responsabilidade unica: ler a serial USB onde o LiDAR esta ligado (via
conversor USB-UART), decodificar os quadros com o parser do driver ativo
(`ld06`, `coin_d6` ou `stl50b2`), manter a varredura polar mais recente e
publicar os SETORES no Event Bus.

Drivers suportados (config `lidar.driver`):
  - "ld06"   (padrao): LD06/LDROBOT, passivo (TX-only), sem comando de start.
  - "coin_d6": Gouke Optronics COIN-D6, bidirecional - exige o comando de
    start `AA 55 F0 0F` apos abrir a serial, senao fica mudo.
  - "stl50b2": STL-50B2 (familia 3iRobotix Delta-2), passivo - gira sozinho
    ao energizar. Quadro de 85 bytes a 230400 baud. **E o unico testado com
    o hardware na mao** (bancada, 2026-09-15).

O que e publicado:
  - `lidar.scan`:   a varredura reduzida a setores (distancia minima por
                    setor) + saude (rpm, contagem de pontos). E o que a
                    navegacao, a web e o bridge ROS2 (Nav2) consomem.
  - `lidar.error`:  falha de leitura/porta, na borda (nao a cada quadro).
  - `lidar.status`: conectou/desconectou a serial.

Nao decide movimento: e sensor, e so publica (mesma regra do Vision Engine).
A checagem tatica de obstaculo com os setores fica no NavigationCore.
"""
from __future__ import annotations

import asyncio
import glob
import logging
import time
from collections import deque
from typing import Any

import serial

from orion.kernel.event_bus import EventBus, Prioridade

from . import coin_d6, ld06, stl50b2

logger = logging.getLogger("motion_core.lidar")

#: Drivers disponiveis: nome em `config/orion.yaml` -> modulo do parser.
DRIVERS = {
    "ld06": ld06,
    "coin_d6": coin_d6,
    "stl50b2": stl50b2,
}
#: Portas tentadas com `lidar.port = "auto"` (ordem importa: USB serial 1o).
PORTAS_CANDIDATAS = (
    "/dev/ttyUSB*",
    "/dev/ttyACM*",
)
#: Janela de quadros guardada para montar a varredura. O LD06 faz ~30 quadros
#: por volta (12 pts) e o COIN-D6 ~50 (8 pts); 45 cobre a volta inteira nos
#: dois casos.
#:
#: O STL-50B2 e bem mais economico: ~13 quadros por volta (25 pts cada), entao
#: 45 guarda ~3,4 VOLTAS, nao uma. Como `acumular_scan` resolve angulo repetido
#: pela MENOR distancia, um obstaculo que ja passou continua aparecendo por
#: ~350 ms a 10 Hz. Isso e conservador para nao bater (erra para "tem coisa
#: ali"), mas nao foi medido em movimento - se aparecer obstaculo fantasma
#: atras do robo andando, a janela por driver e o primeiro lugar a olhar.
QUADROS_NA_JANELA = 45
#: Intervalo minimo entre publicacoes de `lidar.scan` (o LiDAR gira a 10-15 Hz;
#: ninguem precisa de setores 15 vezes por segundo).
INTERVALO_MINIMO_PUBLICACAO_S = 0.4
#: Tempo sem nenhum quadro valido para declarar o LiDAR mudo.
SEM_DADOS_LIMITE_S = 3.0
#: Intervalo entre tentativas de abrir a porta (tolerancia Cap 6 s.8).
REINICIAR_PORTAL_ESPERE_S = 10.0


class LidarManager:
    """Le o LiDAR pela serial USB e publica setores no Event Bus."""

    def __init__(self, event_bus: EventBus, config_lidar: dict[str, Any]) -> None:
        self._event_bus = event_bus
        self._driver = DRIVERS.get(config_lidar.get("driver", "ld06"), ld06)
        #: Prefixo de quadro para resincronizacao (bytes).
        self._cabecalho = getattr(self._driver, "HEADER_INICIO", None)
        if self._cabecalho is None:
            self._cabecalho = bytes((self._driver.HEADER,))
        self._tam_quadro = self._driver.TAMANHO_QUADRO

        self._port = config_lidar.get("port", "auto")
        self._baud = int(config_lidar.get("baud_rate", 230400))
        self._setores = int(config_lidar.get("setores", 12))
        self._min_mm = float(config_lidar.get("min_distancia_mm", 50))
        self._max_mm = float(config_lidar.get("max_distancia_mm", 12000))
        self._confianca_min = int(config_lidar.get("confianca_minima", 0))

        self._porta: serial.Serial | None = None
        self._tarefa: asyncio.Task | None = None
        self._quadros = deque(maxlen=QUADROS_NA_JANELA)
        self._ultima_publicacao_s = 0.0
        self._ultimo_quadro_s = 0.0
        self._ponto_livre_linha = 0
        self._erro_reportado = False
        self._conectado_reportado = False
        self._fim = False

        #: Ultima varredura publicada (para a web consultar sem esperar evento).
        self.scan_mais_recente: dict[str, Any] | None = None
        #: Setores sem eco ja reportados - para logar na borda, nao em enxurrada.
        self._setores_vazios_reportados: tuple[float, ...] = ()

    @property
    def conectado(self) -> bool:
        return self._porta is not None and self._porta.is_open

    async def iniciar(self) -> None:
        """Abre a serial e dispara a tarefa de leitura. Tolera ausencia."""
        self._fim = False
        self._tarefa = asyncio.create_task(self._rodar())

    async def parar(self) -> None:
        self._fim = True
        if self._tarefa is not None:
            self._tarefa.cancel()
            try:
                await self._tarefa
            except (asyncio.CancelledError, Exception):
                pass
            self._tarefa = None
        self._fechar_porta()

    # ---------- tarefa principal ----------

    async def _rodar(self) -> None:
        """Laco de conexao+leitura. Roda como task solta - por isso o catch-all.

        Uma excecao que escapa daqui nao derruba o robo: ela mata ESTA task e
        mais nada acontece - sem log, sem evento, sem reinicio. Foi exatamente
        assim que um `ValueError` no construtor da serial deixou o LiDAR morto
        por semanas parecendo defeito de hardware (2026-09-16). Qualquer coisa
        inesperada aqui precisa GRITAR no log e a task precisa continuar
        tentando; e o unico lugar do modulo onde `except Exception` se
        justifica.
        """
        while not self._fim:
            try:
                if not self.conectado:
                    await self._tentar_abrir()
                    if not self.conectado:
                        await self._reportar_erro("serial_indisponivel")
                        await asyncio.sleep(REINICIAR_PORTAL_ESPERE_S)
                        continue

                await self._ler_loop()
            except asyncio.CancelledError:
                raise  # parar() cancela de proposito - nao e falha
            except serial.SerialException as exc:
                logger.warning("LiDAR: serial caiu (%s) - reconectando", exc)
                self._fechar_porta()
                await asyncio.sleep(1.0)
            except Exception:
                logger.exception("LiDAR: erro inesperado no laco - vou tentar de novo")
                await self._reportar_erro("erro_inesperado")
                self._fechar_porta()
                await asyncio.sleep(REINICIAR_PORTAL_ESPERE_S)

    async def _tentar_abrir(self) -> None:
        if self._port == "auto":
            candidatas = [
                caminho for padrao in PORTAS_CANDIDATAS for caminho in glob.glob(padrao)
            ]
        else:
            candidatas = (self._port,)
        for caminho in candidatas:
            try:
                # `dtr` e `rts` NAO sao parametros do construtor do pyserial -
                # sao propriedades. Passa-los para `serial.Serial(...)` levanta
                # ValueError, que o `except` abaixo NAO pegava: a excecao subia
                # pela task do asyncio que ninguem aguarda e morria calada.
                # Resultado: o LiDAR nunca abriu a porta, em nenhum driver,
                # sem UMA linha de log. Corrigido em 2026-09-16.
                #
                # Configurar com a porta fechada e so entao `open()` e o que de
                # fato evita o pulso em DTR/RTS: alguns conversores USB-UART
                # resetam o dispositivo no flanco, e nenhum dos drivers precisa
                # dessas linhas. Setar depois de abrir geraria justamente o
                # pulso que se quer evitar.
                porta = serial.Serial()
                porta.port = caminho
                porta.baudrate = self._baud
                porta.timeout = 0.2
                porta.rtscts = False
                porta.dsrdtr = False
                porta.dtr = False
                porta.rts = False
                porta.open()
                self._porta = porta
                self._quadros.clear()
                self._ultimo_quadro_s = time.monotonic()
                await self._reportar_conectado(caminho)
                logger.info("LiDAR %s conectado em %s (%d baud)", self._nome_driver, caminho, self._baud)
                self._enviar_comando_iniciar()
                return
            except (serial.SerialException, OSError) as exc:
                logger.debug("LiDAR: falha ao abrir %s: %s", caminho, exc)
        logger.warning("LiDAR: nenhuma porta encontrada em %s", ", ".join(candidatas))

    def _enviar_comando_iniciar(self) -> None:
        """Envia o comando de start do driver, se ele exigir (COIN-D6)."""
        comando = getattr(self._driver, "COMANDO_INICIAR", None)
        if comando is None or self._porta is None:
            return
        try:
            self._porta.write(comando)
            self._porta.flush()
            logger.info("LiDAR %s: comando de start enviado (%s)", self._nome_driver, comando.hex())
        except serial.SerialException as exc:
            logger.warning("LiDAR: falha ao enviar start: %s", exc)

    @property
    def _nome_driver(self) -> str:
        """Nome do driver ativo, so para log.

        Busca reversa em DRIVERS em vez de comparar modulo a modulo: com tres
        drivers, um `if/else` sobre dois deles reportaria o nome ERRADO para o
        terceiro - e log de LiDAR mentindo o modelo e exatamente o tipo de
        pista falsa que custa tempo quando o sensor fica mudo.
        """
        for nome, modulo in DRIVERS.items():
            if modulo is self._driver:
                return nome
        return "desconhecido"

    def _fechar_porta(self) -> None:
        if self._porta is not None:
            try:
                self._porta.close()
            except Exception:
                pass
            self._porta = None
            if self._conectado_reportado:
                logger.warning("LiDAR %s desconectado", self._nome_driver)
                self._conectado_reportado = False

    async def _ler_loop(self) -> None:
        """Consome bytes da serial ate a porta cair ou o manager parar."""
        porta = self._porta
        if porta is None:
            return
        buffer = bytearray()
        while not self._fim and self.conectado:
            dado = await asyncio.to_thread(porta.read, 256)
            if not dado:
                # Silencio prolongado = LiDAR mudo (cabo, alimentacao ou
                # motor parado). Reporta UMA vez, nao a cada iteracao.
                if time.monotonic() - self._ultimo_quadro_s > SEM_DADOS_LIMITE_S:
                    await self._reportar_erro("lidar_sem_dados")
                await asyncio.sleep(0.05)
                continue
            buffer.extend(dado)
            await self._consumir_buffer(buffer)

    async def _consumir_buffer(self, buffer: bytearray) -> None:
        """Extrai todos os quadros completos do buffer, mantendo o resto."""
        while True:
            # Procura o cabecalho de onde quer que esteja (resincronizacao).
            inicio = buffer.find(self._cabecalho) if len(buffer) > 0 else -1
            if inicio < 0:
                buffer.clear()
                return
            if inicio > 0:
                del buffer[:inicio]
            if len(buffer) < self._tam_quadro:
                return
            quadro_bytes = bytes(buffer[: self._tam_quadro])
            del buffer[: self._tam_quadro]
            quadro = self._driver.decodificar_quadro(quadro_bytes)
            if quadro is None:
                # Quadro invalido (CRC ruim ou cabecalho espurio). Conta e deixa
                # o proximo giro do while procurar o proximo cabecalho.
                self._ponto_livre_linha += 1
                continue
            await self._processar_quadro(quadro)

    async def _processar_quadro(self, quadro) -> None:
        self._quadros.append(quadro)
        self._ultimo_quadro_s = time.monotonic()
        agora = time.monotonic()
        if agora - self._ultima_publicacao_s < INTERVALO_MINIMO_PUBLICACAO_S:
            return
        self._ultima_publicacao_s = agora
        scan = self._montar_scan()
        if scan is None:
            return
        self.scan_mais_recente = scan
        await self._event_bus.publish("lidar.scan", scan, prioridade=Prioridade.ALTA)

    def _montar_scan(self) -> dict[str, Any] | None:
        """Converte a janela de quadros em setores (distancia minima por setor).

        Só publica quando a janela cobre TODOS os setores (uma varredura
        razoavelmente completa). Sem cobertura total, os setores ficam cheios
        de None e a navegacao trataria "nao sei" como vazio.
        """
        scan = self._driver.acumular_scan(list(self._quadros))
        if not scan:
            return None

        largura = 360.0 / self._setores
        setores: list[dict[str, Any]] = []
        cobertos = 0
        for i in range(self._setores):
            inicio = i * largura
            fim = inicio + largura
            distancia_mm = None
            for ponto in scan.values():
                if (
                    inicio <= ponto.angulo_graus < fim
                    and self._min_mm <= ponto.distancia_mm <= self._max_mm
                    and ponto.confianca >= self._confianca_min
                ):
                    if distancia_mm is None or ponto.distancia_mm < distancia_mm:
                        distancia_mm = ponto.distancia_mm
            if distancia_mm is not None:
                cobertos += 1
            setores.append(
                {
                    "inicio_graus": round(inicio, 1),
                    "fim_graus": round(fim, 1),
                    "distancia_cm": None if distancia_mm is None else round(distancia_mm / 10.0, 1),
                }
            )

        if cobertos == 0:
            # NENHUM setor mediu: nao ha varredura nenhuma para entregar. Este
            # e o unico caso que ainda segura a publicacao.
            return None

        if cobertos < self._setores:
            # UM SETOR TEIMOSO NAO CEGA O ROBO INTEIRO (2026-09-19). Ate aqui
            # isto era `return None`: bastava UM dos 12 setores sem eco e a
            # varredura inteira era descartada - inclusive a leitura boa da
            # frente. Medido ao vivo nesse dia, com o robo parado numa sala:
            #
            #   nao publiquei - 2 de 12 setores sem eco (graus: 180, 210)
            #   nao publiquei - 1 de 12 setores sem eco (graus: 150)
            #
            # 180 e 210 sao ATRAS. "Nao sei o que ha atras" estava virando
            # "nao sei nada", e o robo ficava sem distancia frontal por causa
            # de um setor que nao tem nada a ver com andar para a frente.
            #
            # O medo original ("a navegacao trataria 'nao sei' como vazio") ja
            # estava resolvido do lado de quem consome: cada setor carrega o
            # proprio `distancia_cm: None`, e `_menor_distancia_lidar_frontal`
            # (navigation_core) PULA setor sem medida em vez de achar que esta
            # livre. Suprimir a varredura inteira protegia contra um erro que
            # ninguem mais cometia, e cobrava a cegueira como preco.
            #
            # Reporta so na BORDA (quando a lista de setores vazios muda), nao
            # a cada varredura - senao vira enxurrada a 2,5 Hz.
            vazios = tuple(
                s["inicio_graus"] for s in setores if s["distancia_cm"] is None
            )
            if vazios != self._setores_vazios_reportados:
                self._setores_vazios_reportados = vazios
                logger.info(
                    "LiDAR %s: publicando com %d de %d setores sem eco (graus: %s). "
                    "Sem eco = PERTO DEMAIS (abaixo de %.0f mm), alem do alcance, "
                    "vidro ou angulo rasante - nunca 'caminho livre'.",
                    self._nome_driver,
                    len(vazios),
                    self._setores,
                    ", ".join(f"{g:.0f}" for g in vazios),
                    self._min_mm,
                )
        elif self._setores_vazios_reportados:
            logger.info("LiDAR %s: cobertura completa de novo", self._nome_driver)
            self._setores_vazios_reportados = ()

        # rpm = velocidade em graus/s / 360 * 60. Usa a media da janela.
        velocidades = [q.velocidade_graus_s for q in self._quadros]
        velocidade_media = sum(velocidades) / len(velocidades) if velocidades else 0.0
        return {
            "valido": True,
            "setores": setores,
            # Quantos dos setores trazem medida de verdade. Vai junto para quem
            # le a varredura poder dizer "parcial" em vez de fingir que 12 de 12
            # mediram - a varredura agora sai mesmo incompleta (ver acima).
            "setores_cobertos": cobertos,
            "setores_total": self._setores,
            "pontos": len(scan),
            "rpm": round(velocidade_media / 360.0 * 60.0, 1),
            "timestamp_s": round(time.monotonic(), 2),
        }

    # ---------- eventos ----------

    async def _reportar_conectado(self, caminho: str) -> None:
        self._erro_reportado = False
        self._conectado_reportado = True
        await self._event_bus.publish(
            "lidar.status", {"conectado": True, "porta": caminho}, prioridade=Prioridade.NORMAL
        )

    async def _reportar_erro(self, motivo: str) -> None:
        if self._erro_reportado:
            return  # na borda, nao a cada ciclo
        self._erro_reportado = True
        await self._event_bus.publish(
            "lidar.error", {"motivo": motivo}, prioridade=Prioridade.ALTA
        )