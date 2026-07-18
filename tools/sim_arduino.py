#!/usr/bin/env python3
"""Simulador do Arduino Mega / Hardware Core (Fase 2, Cap 5, 10, 14).

Expoe uma porta serial virtual (par de pseudo-terminais) que fala o mesmo
enquadramento/protocolo do firmware real (STX/ETX/escape + CRC16): responde
WHO_ARE_YOU, ACKa todo COMMAND, responde RETURN_STATUS e envia HEARTBEAT
periodico - o suficiente para testar o Raspberry (Motion Core) sem o Mega
fisico.

Uso:
    python tools/sim_arduino.py
    # imprime o caminho da ponta "escravo" (ex.: /dev/pts/7) - aponte o
    # SerialTransport do Raspberry para esse caminho.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orion.communication.framing import DecodificadorSerial, codificar_serial  # noqa: E402
from orion.communication.protocol import (  # noqa: E402
    VERSAO_PROTOCOLO,
    ErroProtocoloInvalido,
    Mensagem,
    TipoMensagem,
)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sim_arduino")

NOME_MODULO = "hardware_core"
VERSAO_SIMULADOR = "sim-0.1.0"
INTERVALO_HEARTBEAT_S = 1.0


class ArduinoSimulado:
    """Fala o protocolo diretamente sobre o fd "mestre" do pty - o lado
    "escravo" e quem o Raspberry abre com um SerialTransport de verdade."""

    def __init__(self, master_fd: int) -> None:
        self._master_fd = master_fd
        self._decodificador = DecodificadorSerial()

    def _escrever(self, mensagem: Mensagem) -> None:
        os.write(self._master_fd, codificar_serial(mensagem.to_bytes()))

    async def ler_continuamente(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            dados: bytes = await loop.run_in_executor(None, os.read, self._master_fd, 4096)
            if not dados:
                continue
            for byte in dados:
                quadro = self._decodificador.alimentar(byte)
                if quadro is not None:
                    self._tratar(quadro)

    async def enviar_heartbeats(self) -> None:
        while True:
            await asyncio.sleep(INTERVALO_HEARTBEAT_S)
            self._escrever(Mensagem.nova(TipoMensagem.HEARTBEAT, NOME_MODULO, "motion_core"))

    def _tratar(self, quadro: bytes) -> None:
        try:
            mensagem = Mensagem.from_bytes(quadro)
        except ErroProtocoloInvalido:
            logger.warning("Quadro invalido recebido, ignorado")
            return

        if not mensagem.checksum_valido():
            logger.warning("Checksum invalido, enviando NACK")
            self._escrever(Mensagem.nack(mensagem, NOME_MODULO, "checksum_invalido"))
            return

        logger.info("Recebido: tipo=%s payload=%s", mensagem.tipo.value, mensagem.payload)

        if mensagem.tipo is TipoMensagem.COMMAND:
            self._escrever(Mensagem.ack(mensagem, NOME_MODULO))
            self._responder_comando(mensagem)

    def _responder_comando(self, mensagem: Mensagem) -> None:
        comando = mensagem.payload.get("comando")
        if comando == "WHO_ARE_YOU":
            payload = {
                "nome": NOME_MODULO,
                "versao_modulo": VERSAO_SIMULADOR,
                "versao_protocolo": VERSAO_PROTOCOLO,
            }
        elif comando == "RETURN_STATUS":
            payload = {"estado": "READY", "bateria_percent": 100, "simulado": True}
        else:
            return
        self._escrever(
            Mensagem.nova(
                TipoMensagem.RESPONSE,
                NOME_MODULO,
                mensagem.origem,
                payload,
                id_referencia=mensagem.id,
            )
        )


async def main() -> None:
    master_fd, escravo_fd = os.openpty()
    caminho_escravo = os.ttyname(escravo_fd)
    print(f"Arduino simulado pronto. Porta serial virtual: {caminho_escravo}")
    print(f'Aponte o SerialTransport do Raspberry para "{caminho_escravo}".')
    logger.info("Escutando em %s", caminho_escravo)

    simulado = ArduinoSimulado(master_fd)
    try:
        await asyncio.gather(simulado.ler_continuamente(), simulado.enviar_heartbeats())
    finally:
        os.close(master_fd)
        os.close(escravo_fd)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nEncerrando simulador...")
