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
        try:
            self._writer.write(codificar_tcp(payload))
            await self._writer.drain()
        except OSError as erro:
            # Diferente de conectar(), isso nao tinha tratamento - uma queda
            # de rede no meio do envio (ConnectionResetError, BrokenPipeError,
            # ambos OSError) subia cru, e nenhum chamador em service.py espera
            # isso (so ErroComunicacao/asyncio.TimeoutError). No WiFi de
            # desenvolvimento deste projeto (documentado como instavel no
            # proprio orion.yaml), isso podia derrubar o boot inteiro em vez
            # de virar uma falha tolerada. Achado real da vistoria de
            # 2026-07-24.
            raise ErroTransporte(f"Falha ao enviar em {self._host}:{self._port}: {erro}") from erro

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
        try:
            self._writer.write(codificar_tcp(payload))
            await self._writer.drain()
        except OSError as erro:
            # Mesmo achado do TcpTransport.enviar() (vistoria de 2026-07-24):
            # o outro lado (Notebook) pode derrubar a conexao no meio do
            # envio - sem isso, o erro subia cru em vez de ErroTransporte.
            raise ErroTransporte(f"Falha ao enviar pela conexao TCP: {erro}") from erro

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
        try:
            # O executor tem UMA thread (ver __init__: serializar read/write e
            # obrigatorio, pyserial corrompe a porta com acesso concorrente).
            # Consequencia: este write entra na FILA atras do read() que o
            # receber() mantem em andamento - e esse read so volta quando chega
            # byte ou quando estoura o timeout_leitura_s.
            #
            # MEDIDO (2026-07-28): isso custava ~100ms para o write sair, mais
            # ~100ms para o ACK ser lido no ciclo seguinte. Um comando com ACK
            # levava 208ms (mediana de 20, desvio minimo) quando a ida e volta
            # real a 250000 baud e de poucos ms. O joystick do dashboard repete
            # a cada ~150ms: como CADA comando custava mais que o intervalo de
            # chegada do proximo, a fila crescia sem limite e o operador via
            # "trava quando dou varios comandos seguidos".
            #
            # A correcao e a MESMA ja usada no fechar() (achado de 2026-07-27):
            # cancel_read() interrompe o read() em andamento sendo chamado de
            # OUTRA thread (aqui, a do event loop) sem precisar entrar na fila
            # do executor - e sem quebrar a serializacao, porque continua tudo
            # na mesma thread de I/O.
            #
            # ORDEM IMPORTA: submeter o write PRIMEIRO (ele fica na fila logo
            # atras do read atual) e so entao cancelar a leitura. O executor e
            # FIFO, entao o write roda assim que o read volta, antes de o laco
            # de receber() submeter o proximo. Cancelar antes de submeter
            # abriria a janela para o read seguinte entrar na frente.
            #
            # Nao perde quadro: o decodificador e alimentado byte a byte e
            # guarda estado, entao um read() que volta com dado parcial e
            # exatamente o caso normal entre bytes.
            futuro = loop.run_in_executor(
                self._executor, self._serial.write, codificar_serial(payload)
            )
            if not self._parar_leitura:
                self._serial.cancel_read()
            await futuro
        except serial.SerialException as erro:
            # Mesmo achado do TcpTransport.enviar() (vistoria de 2026-07-24):
            # o Arduino pode ser desconectado/resetado no meio do envio -
            # sem isso, o erro subia cru em vez de ErroTransporte.
            raise ErroTransporte(f"Falha ao enviar pela porta serial {self._porta}: {erro}") from erro

    async def receber(self) -> AsyncIterator[bytes]:
        if self._serial is None:
            raise ErroTransporte("Transporte serial nao conectado")
        loop = asyncio.get_running_loop()

        def _ler_disponivel() -> bytes:
            # read(N) so volta quando junta N BYTES ou quando estoura o
            # timeout. Pedir 4096 de uma vez - como se fazia ate 2026-07-28 -
            # significa que um quadro pequeno (um ACK tem poucas dezenas de
            # bytes) ficava PARADO ate o timeout inteiro de 100ms, mesmo tendo
            # chegado em poucos ms. Era metade do custo de um comando com ACK.
            #
            # Bloquear por 1 byte e depois drenar o que ja esta no buffer
            # devolve o quadro assim que ele chega, sem espera artificial e sem
            # spin: a espera continua sendo no read(), nao num laco ocupado.
            primeiro = self._serial.read(1)
            if not primeiro:
                return b""
            restante = self._serial.in_waiting
            return primeiro + self._serial.read(restante) if restante else primeiro

        while not self._parar_leitura:
            dados: bytes = await loop.run_in_executor(self._executor, _ler_disponivel)
            if not dados:
                continue  # timeout de leitura - normal, so verifica se deve parar
            for byte in dados:
                quadro = self._decodificador.alimentar(byte)
                if quadro is not None:
                    yield quadro

    async def fechar(self) -> None:
        self._parar_leitura = True
        if self._serial is not None:
            # BUG REAL (2026-07-27, vistoria pos-gravacao de firmware): o
            # executor tem UMA thread so (comentario em __init__). Se
            # receber() estiver bloqueado dentro de self._serial.read() -
            # legitimo, e o estado normal entre bytes - o close() abaixo
            # entra na FILA atras dele, porque e o mesmo executor. Na
            # bancada isso travou o desligamento gracioso do Motion Core por
            # mais de 80s (o read() nao voltou nem apos seu proprio timeout
            # configurado), ate o systemd mandar SIGKILL e o
            # `Restart=always` subir um processo novo. cancel_read() existe
            # exatamente para isto: interrompe um read() em andamento sendo
            # chamado de OUTRA thread (aqui, a do event loop), sem precisar
            # entrar na fila do executor - so depois disso o close() e
            # submetido, com a thread ja livre.
            self._serial.cancel_read()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._serial.close)
        self._executor.shutdown(wait=False)
