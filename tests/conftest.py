"""Fixtures/helpers compartilhados entre tests/unit e tests/integration."""
import asyncio


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
