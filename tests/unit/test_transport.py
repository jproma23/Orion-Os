"""Testes dos transportes (Cap 14 s.2-3).

TCP e testado com um servidor de loopback real (127.0.0.1). Serial e
testado com um par de pseudo-terminais (pty) conectados - a mesma tecnica
que os simuladores da Fase 2 (tools/sim_arduino.py) vao usar para expor uma
"porta serial virtual" sem hardware real.
"""
import asyncio
import os

import pytest

from orion.communication.transport import (
    ConexaoTcp,
    SerialTransport,
    TcpTransport,
    iniciar_servidor_tcp,
)


@pytest.mark.asyncio
async def test_tcp_transport_envia_e_recebe():
    recebido_pelo_servidor = asyncio.Queue()
    conexoes: list[ConexaoTcp] = []

    async def ao_conectar(conexao: ConexaoTcp) -> None:
        conexoes.append(conexao)

        async def _ler():
            async for mensagem in conexao.receber():
                await recebido_pelo_servidor.put(mensagem)

        asyncio.create_task(_ler())

    servidor = await iniciar_servidor_tcp("127.0.0.1", 0, ao_conectar)
    porta = servidor.sockets[0].getsockname()[1]

    cliente = TcpTransport("127.0.0.1", porta)
    await cliente.conectar()
    await cliente.enviar(b"ola servidor")

    mensagem = await asyncio.wait_for(recebido_pelo_servidor.get(), timeout=2)
    assert mensagem == b"ola servidor"

    # resposta do servidor de volta ao cliente
    await conexoes[0].enviar(b"ola cliente")
    receptor_cliente = cliente.receber()
    resposta = await asyncio.wait_for(receptor_cliente.__anext__(), timeout=2)
    assert resposta == b"ola cliente"

    await cliente.fechar()
    await conexoes[0].fechar()
    servidor.close()
    await servidor.wait_closed()


@pytest.mark.asyncio
async def test_tcp_transport_falha_ao_conectar_em_porta_fechada():
    from orion.communication.transport import ErroTransporte

    cliente = TcpTransport("127.0.0.1", 1)  # porta privilegiada, ninguem escutando
    with pytest.raises(ErroTransporte):
        await cliente.conectar()


@pytest.mark.asyncio
async def test_serial_transport_envia_e_recebe_via_pty():
    mestre_fd, escravo_fd = os.openpty()
    caminho_escravo = os.ttyname(escravo_fd)

    transporte = SerialTransport(
        caminho_escravo, baud_rate=115200, timeout_leitura_s=0.05, atraso_reset_s=0
    )
    await transporte.conectar()

    # ORION (via SerialTransport) -> escravo -> mestre: le do lado "arduino simulado"
    await transporte.enviar(b'{"tipo":"HEARTBEAT"}')
    await asyncio.sleep(0.05)
    bruto = os.read(mestre_fd, 4096)
    assert b'{"tipo":"HEARTBEAT"}' in bruto  # quadro codificado contem o payload

    # mestre (arduino simulado) -> escravo -> ORION: escreve e le via receber()
    from orion.communication.framing import codificar_serial

    os.write(mestre_fd, codificar_serial(b'{"tipo":"ACK"}'))

    receptor = transporte.receber()
    quadro = await asyncio.wait_for(receptor.__anext__(), timeout=2)
    assert quadro == b'{"tipo":"ACK"}'

    await transporte.fechar()
    os.close(mestre_fd)


@pytest.mark.asyncio
async def test_serial_transport_falha_ao_abrir_porta_inexistente():
    from orion.communication.transport import ErroTransporte

    transporte = SerialTransport("/dev/nao_existe_xyz", baud_rate=115200)
    with pytest.raises(ErroTransporte):
        await transporte.conectar()


@pytest.mark.asyncio
async def test_tcp_transport_enviar_converte_oserror_em_erro_transporte():
    """Achado real da vistoria de 2026-07-24: uma queda de rede no MEIO do
    envio (ConnectionResetError/BrokenPipeError, ambas OSError) subia crua
    antes deste fix - nenhum chamador em service.py espera OSError, so
    ErroTransporte/asyncio.TimeoutError."""
    from orion.communication.transport import ErroTransporte

    async def ao_conectar(_conexao: ConexaoTcp) -> None:
        pass

    servidor = await iniciar_servidor_tcp("127.0.0.1", 0, ao_conectar)
    porta = servidor.sockets[0].getsockname()[1]

    cliente = TcpTransport("127.0.0.1", porta)
    await cliente.conectar()

    async def _drain_com_falha() -> None:
        raise ConnectionResetError("conexao resetada pelo outro lado (simulado)")

    cliente._writer.drain = _drain_com_falha  # type: ignore[method-assign]

    with pytest.raises(ErroTransporte):
        await cliente.enviar(b"vai falhar no meio do envio")

    await cliente.fechar()
    servidor.close()
    await servidor.wait_closed()


@pytest.mark.asyncio
async def test_serial_transport_enviar_converte_serial_exception_em_erro_transporte():
    """Mesmo achado do teste TCP acima, mas para o link com o Arduino: o
    Mega pode ser desconectado/resetado no meio do envio."""
    import serial as pyserial

    from orion.communication.transport import ErroTransporte

    mestre_fd, escravo_fd = os.openpty()
    caminho_escravo = os.ttyname(escravo_fd)

    transporte = SerialTransport(
        caminho_escravo, baud_rate=115200, timeout_leitura_s=0.05, atraso_reset_s=0
    )
    await transporte.conectar()

    def _write_com_falha(_dados: bytes) -> int:
        raise pyserial.SerialException("porta desconectada (simulado)")

    transporte._serial.write = _write_com_falha  # type: ignore[method-assign]

    with pytest.raises(ErroTransporte):
        await transporte.enviar(b'{"tipo":"HEARTBEAT"}')

    await transporte.fechar()
    os.close(mestre_fd)


@pytest.mark.asyncio
async def test_serial_transport_fechar_nao_espera_read_pendente():
    """Achado real (2026-07-27, vistoria pos-gravacao de firmware no Mega):
    fechar() mandava self._serial.close para o MESMO executor de 1 thread
    que receber() usa para o read() bloqueante (ver comentario no
    __init__) - se o read() estivesse em andamento, close() ficava na fila
    atras dele. Em producao isso travou o desligamento gracioso do Motion
    Core por mais de 80s (o read() nao voltou nem apos seu proprio timeout
    configurado), so resolvido quando o systemd matou o processo a forca
    (SIGKILL, sem TimeoutStopSec configurado -> ~90s de default) e
    `Restart=always` subiu um processo novo.

    Reproduzido aqui com um timeout de leitura de 3s (bem maior que o
    padrao de producao, 0.1s) e nenhum dado chegando no pty - sem
    cancel_read(), fechar() ficaria bloqueado ate o read() estourar esse
    timeout sozinho. Com cancel_read(), tem que retornar quase na hora,
    porque a leitura pendente e interrompida de fora, sem depender do
    timeout dela."""
    mestre_fd, escravo_fd = os.openpty()
    caminho_escravo = os.ttyname(escravo_fd)

    transporte = SerialTransport(
        caminho_escravo, baud_rate=115200, timeout_leitura_s=3.0, atraso_reset_s=0
    )
    await transporte.conectar()

    async def _consumir() -> None:
        async for _ in transporte.receber():
            pass

    tarefa_leitura = asyncio.create_task(_consumir())
    await asyncio.sleep(0.1)  # garante que a tarefa ja esta bloqueada no read()

    inicio = asyncio.get_running_loop().time()
    await asyncio.wait_for(transporte.fechar(), timeout=1.0)
    duracao = asyncio.get_running_loop().time() - inicio
    assert duracao < 1.0  # nao esperou os 3s do timeout de leitura

    tarefa_leitura.cancel()
    try:
        await tarefa_leitura
    except asyncio.CancelledError:
        pass
    os.close(mestre_fd)
