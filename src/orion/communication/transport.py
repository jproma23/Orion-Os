"""Transportes do Communication Core (Cap 14 secao 2-3).

Duas variantes sob a mesma interface: TcpTransport (Notebook->Raspberry,
lado cliente) e SerialTransport (Raspberry<->Arduino). Os modulos nunca
acessam sockets ou portas seriais diretamente (Cap 14 secao 7) - so atraves
destas classes, que ja entregam/recebem quadros completos (bytes de uma
mensagem), sem o chamador se preocupar com enquadramento.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Awaitable, Callable, Protocol

import serial

from orion.communication.framing import (
    DecodificadorSerial,
    DecodificadorTcp,
    codificar_serial,
    codificar_tcp,
)

logger = logging.getLogger("orion.communication.transport")


class ErroTransporte(Exception):
    """Falha ao conectar, enviar ou receber em um transporte."""


class Transporte(Protocol):
    """Interface comum aos transportes: enviar/receber quadros completos (bytes)."""

    @property
    def conectado(self) -> bool: ...

    async def enviar(self, payload: bytes) -> None: ...

    def receber(self) -> AsyncIterator[bytes]: ...

    async def fechar(self) -> None: ...


class TcpTransport:
    """Cliente TCP: usado pelo Notebook para falar com o Raspberry (Cap 14 s.2)."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._decodificador = DecodificadorTcp()

    @property
    def conectado(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def conectar(self) -> None:
        try:
            self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        except OSError as erro:
            raise ErroTransporte(
                f"Falha ao conectar em {self._host}:{self._port}: {erro}"
            ) from erro

    async def enviar(self, payload: bytes) -> None:
        if not self.conectado:
            raise ErroTransporte("Transporte TCP nao conectado")
        assert self._writer is not None
        self._writer.write(codificar_tcp(payload))
        await self._writer.drain()

    async def receber(self) -> AsyncIterator[bytes]:
        if self._reader is None:
            raise ErroTransporte("Transporte TCP nao conectado")
        while True:
            dados = await self._reader.read(4096)
            if not dados:
                return  # conexao fechada pelo outro lado
            for mensagem in self._decodificador.alimentar(dados):
                yield mensagem

    async def fechar(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass


class ConexaoTcp:
    """Envolve uma conexao TCP ja aceita (lado servidor - usado pelo Raspberry
    ao aceitar o Notebook, e pelos simuladores de Fase 2)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._decodificador = DecodificadorTcp()

    @property
    def conectado(self) -> bool:
        return not self._writer.is_closing()

    async def enviar(self, payload: bytes) -> None:
        self._writer.write(codificar_tcp(payload))
        await self._writer.drain()

    async def receber(self) -> AsyncIterator[bytes]:
        while True:
            dados = await self._reader.read(4096)
            if not dados:
                return
            for mensagem in self._decodificador.alimentar(dados):
                yield mensagem

    async def fechar(self) -> None:
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except OSError:
            pass


async def iniciar_servidor_tcp(
    host: str,
    port: int,
    ao_conectar: Callable[[ConexaoTcp], Awaitable[None]],
) -> asyncio.AbstractServer:
    """Sobe um servidor TCP; para cada conexao aceita, chama `ao_conectar` com
    a ConexaoTcp correspondente (tipicamente cria uma task para trata-la)."""

    async def _lidar_com_cliente(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await ao_conectar(ConexaoTcp(reader, writer))

    return await asyncio.start_server(_lidar_com_cliente, host, port)


class SerialTransport:
    """Porta serial: usada pelo Raspberry para falar com o Arduino Mega
    (Cap 14 s.2). pyserial e sincrono - leitura e escrita rodam em thread
    separada (executor) para nao bloquear o event loop."""

    def __init__(
        self,
        porta: str,
        baud_rate: int,
        timeout_leitura_s: float = 0.1,
        atraso_reset_s: float = 2.0,
    ) -> None:
        self._porta = porta
        self._baud_rate = baud_rate
        self._timeout_leitura_s = timeout_leitura_s
        # Abrir a porta serial ativa o DTR e reseta o Arduino (comportamento
        # padrao da maioria dos adaptadores USB-serial, incluindo o CH340 do
        # Mega deste projeto) - o bootloader + setup() levam ~1-2s para o
        # firmware ficar pronto para receber. Sem essa espera, comandos
        # enviados logo apos conectar() se perdem (visto na pratica: WHO_ARE_YOU
        # sem resposta). 0 desliga a espera (ex.: pty de teste, que nao reseta).
        self._atraso_reset_s = atraso_reset_s
        self._serial: serial.Serial | None = None
        self._decodificador = DecodificadorSerial()
        self._parar_leitura = False
        # pyserial nao e thread-safe para leitura e escrita concorrentes na
        # mesma porta: usar o executor padrao (multi-thread) do asyncio para
        # read() e write() ao mesmo tempo corrompe o estado interno da porta
        # (visto na pratica: self.fd virando None em pyserial 3.5 durante um
        # read() enquanto um write() rodava em outra thread). Uma unica
        # thread dedicada serializa todo acesso a porta e elimina a corrida.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="serial-io")

    @property
    def conectado(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def quadros_invalidos(self) -> int:
        return self._decodificador.quadros_invalidos

    async def conectar(self) -> None:
        loop = asyncio.get_running_loop()

        def _abrir() -> serial.Serial:
            return serial.Serial(self._porta, self._baud_rate, timeout=self._timeout_leitura_s)

        try:
            self._serial = await loop.run_in_executor(self._executor, _abrir)
        except serial.SerialException as erro:
            raise ErroTransporte(f"Falha ao abrir porta serial {self._porta}: {erro}") from erro

        if self._atraso_reset_s > 0:
            await asyncio.sleep(self._atraso_reset_s)

    async def enviar(self, payload: bytes) -> None:
        if self._serial is None or not self.conectado:
            raise ErroTransporte("Transporte serial nao conectado")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._serial.write, codificar_serial(payload))

    async def receber(self) -> AsyncIterator[bytes]:
        if self._serial is None:
            raise ErroTransporte("Transporte serial nao conectado")
        loop = asyncio.get_running_loop()
        while not self._parar_leitura:
            dados: bytes = await loop.run_in_executor(self._executor, self._serial.read, 4096)
            if not dados:
                continue  # timeout de leitura - normal, so verifica se deve parar
            for byte in dados:
                quadro = self._decodificador.alimentar(byte)
                if quadro is not None:
                    yield quadro

    async def fechar(self) -> None:
        self._parar_leitura = True
        if self._serial is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._serial.close)
        self._executor.shutdown(wait=False)
