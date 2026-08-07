"""Fixtures/helpers compartilhados entre tests/unit e tests/integration."""
import asyncio
import os

from orion.communication.framing import DecodificadorSerial, codificar_serial
from orion.communication.protocol import Mensagem, TipoMensagem


class FakeTransporte:
    """Transporte em memoria (sem socket/serial real): `enviar` empurra para
    uma fila que o outro lado (ou o proprio teste) pode ler; `injetar` simula
    uma mensagem chegando de fora."""

    def __init__(self) -> None:
        self.enviados: list[bytes] = []
        self._entrada: asyncio.Queue = asyncio.Queue()
        self._conectado = True

    @property
    def conectado(self) -> bool:
        return self._conectado

    async def enviar(self, payload: bytes) -> None:
        self.enviados.append(payload)

    async def injetar(self, payload: bytes) -> None:
        await self._entrada.put(payload)

    async def receber(self):
        while True:
            dados = await self._entrada.get()
            yield dados

    async def fechar(self) -> None:
        self._conectado = False

# Simulador do Hardware Core numa pty: responde ACK e WHO_ARE_YOU.
# Mora AQUI e nao num modulo de teste porque dois arquivos o usam, e
# `tests` nao e pacote importavel - importar de um test_*.py para
# outro quebra a coleta (aconteceu no Notebook em 06/08).
async def arduino_simulado(master_fd: int) -> None:
    loop = asyncio.get_running_loop()
    decodificador = DecodificadorSerial()
    fila: asyncio.Queue[bytes] = asyncio.Queue()

    def _ao_ter_dados() -> None:
        try:
            fila.put_nowait(os.read(master_fd, 4096))
        except OSError:
            pass

    loop.add_reader(master_fd, _ao_ter_dados)
    try:
        while True:
            dados = await fila.get()
            for byte in dados:
                quadro = decodificador.alimentar(byte)
                if quadro is None:
                    continue
                pedido = Mensagem.from_bytes(quadro)
                if pedido.tipo is TipoMensagem.COMMAND:
                    ack = Mensagem.ack(pedido, "hardware_core")
                    os.write(master_fd, codificar_serial(ack.to_bytes()))
                    if pedido.payload.get("comando") == "WHO_ARE_YOU":
                        resposta = Mensagem.nova(
                            TipoMensagem.RESPONSE,
                            "hardware_core",
                            pedido.origem,
                            {
                                "nome": "hardware_core",
                                "versao_modulo": "test-sim",
                                "versao_protocolo": "1.0",
                            },
                            id_referencia=pedido.id,
                        )
                        os.write(master_fd, codificar_serial(resposta.to_bytes()))
    finally:
        loop.remove_reader(master_fd)
