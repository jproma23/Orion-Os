"""Ponte entre o Event Bus e o rosto ESP32-S3 (orion_face), por USB serial.

A placa e so um periferico de SAIDA (rosto + voz) - decisao do dono
(2026-09-12): a rede WiFi que o orion_face usava antes esbarrou num
bloqueio fora do nosso controle (roteador do provedor, sem acesso). O cabo
USB que ja liga fisicamente a placa ao Pi carrega os eventos no lugar -
ver orion_face/src/net.cpp: handle_line() consome exatamente este formato.
"""
from __future__ import annotations

import json
import logging

import serial

from orion.kernel.event_bus import EventBus, Evento

logger = logging.getLogger("orion.display.ponte_serial")

#: eventos que o firmware do orion_face realmente reage (ver
#: orion_face/src/net.cpp: handle_line)
TOPICOS_REPASSADOS = (
    "voice.response_started",
    "voice.response_finished",
    "motion.pan_tilt",
)


class PonteSerial:
    """Escreve cada evento repassado como uma linha JSON na porta serial do
    orion_face. Nunca levanta: porta ausente ou fechada so registra e segue
    (Cap 6 s.8 - modulo ausente nao aborta o sistema)."""

    def __init__(
        self,
        event_bus: EventBus,
        porta: str = "/dev/ttyACM0",
        baud: int = 115200,
    ) -> None:
        self._event_bus = event_bus
        self._porta = porta
        self._baud = baud
        self._serial: serial.Serial | None = None

    async def iniciar(self) -> None:
        try:
            self._serial = serial.Serial(self._porta, self._baud, timeout=0)
        except serial.SerialException as erro:
            logger.warning("orion_face inalcancavel em %s: %s", self._porta, erro)
            self._serial = None
            return
        for topico in TOPICOS_REPASSADOS:
            self._event_bus.subscribe(topico, self._fazer_repassador(topico))
        logger.info("Ponte serial com orion_face pronta (%s)", self._porta)

    def _fazer_repassador(self, topico: str):
        async def _repassar(evento: Evento) -> None:
            if self._serial is None:
                return
            linha = json.dumps({"evento": topico, **evento.dados}) + "\n"
            try:
                self._serial.write(linha.encode("utf-8"))
            except serial.SerialException:
                logger.warning("Falha ao escrever no orion_face, ponte segue tentando")

        return _repassar

    async def encerrar(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None
