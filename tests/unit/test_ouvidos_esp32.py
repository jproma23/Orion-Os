"""Testes dos ouvidos do ESP32 (Cap 9) - o cliente do stream I2S por WiFi.

Sem ESP32 e sem rede: sobe um servidor TCP de mentira que fala o mesmo
protocolo (int16 LE intercalado a 16 kHz). O que se testa aqui é a FIAÇÃO,
que é onde os defeitos deste projeto moram - não a aritmética.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest

from orion.voice.captura_audio import ErroAudio
from orion.voice.ouvidos_esp32 import OuvidosESP32


def _quadros(esquerdo: np.ndarray, direito: np.ndarray) -> bytes:
    """Intercala dois canais como o firmware do ESP32 faz."""
    intercalado = np.empty(esquerdo.size + direito.size, dtype="<i2")
    intercalado[0::2] = (esquerdo * 32767).astype("<i2")
    intercalado[1::2] = (direito * 32767).astype("<i2")
    return intercalado.tobytes()


class ESP32Falso:
    """Servidor que empurra áudio como o ESP32 - inclusive sem parar."""

    def __init__(self, esquerdo_amp=0.5, direito_amp=0.1, taxa=16000):
        self.esquerdo_amp = esquerdo_amp
        self.direito_amp = direito_amp
        self.taxa = taxa
        self.servidor = None
        self.porta = None
        self.conexoes = 0
        self.derrubar_na_conexao = False

    async def iniciar(self):
        self.servidor = await asyncio.start_server(self._atender, "127.0.0.1", 0)
        self.porta = self.servidor.sockets[0].getsockname()[1]
        return self

    async def _atender(self, leitor, escritor):
        self.conexoes += 1
        if self.derrubar_na_conexao:
            escritor.close()
            return
        try:
            passo = 0
            while True:
                # 0,1s de áudio por pacote, como um stream de verdade
                n = self.taxa // 10
                t = (np.arange(n) + passo * n) / self.taxa
                esq = self.esquerdo_amp * np.sin(2 * np.pi * 440 * t)
                dir_ = self.direito_amp * np.sin(2 * np.pi * 440 * t)
                escritor.write(_quadros(esq, dir_))
                await escritor.drain()
                passo += 1
                await asyncio.sleep(0.1)
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass

    async def parar(self):
        self.servidor.close()
        await self.servidor.wait_closed()


@pytest.mark.asyncio
async def test_grava_audio_mono_na_duracao_pedida():
    esp = await ESP32Falso().iniciar()
    ouvidos = OuvidosESP32("127.0.0.1", esp.porta)
    await ouvidos.iniciar()
    try:
        await asyncio.sleep(0.3)
        audio = await ouvidos.gravar(0.5)
        assert audio.ndim == 1, "o Whisper espera mono"
        assert audio.dtype == np.float32
        assert abs(len(audio) - 8000) <= 1, "0,5s a 16kHz = 8000 amostras"
    finally:
        await ouvidos.encerrar()
        await esp.parar()


@pytest.mark.asyncio
async def test_usa_o_canal_mais_forte_e_nao_a_soma():
    """Somar dois mics separados no espaço cria filtro pente e abafa a fala.
    O canal escolhido tem que ser o do lado que ouve melhor."""
    esp = await ESP32Falso(esquerdo_amp=0.05, direito_amp=0.6).iniciar()
    ouvidos = OuvidosESP32("127.0.0.1", esp.porta)
    await ouvidos.iniciar()
    try:
        await asyncio.sleep(0.3)
        audio = await ouvidos.gravar(0.4)
        rms = float(np.sqrt(np.mean(audio**2)))
        # o direito (0,6) tem rms ~0,42; o esquerdo (0,05) ~0,035
        assert rms > 0.3, f"pegou o canal fraco (rms={rms:.3f})"
    finally:
        await ouvidos.encerrar()
        await esp.parar()


@pytest.mark.asyncio
async def test_nao_devolve_audio_VELHO():
    """O erro fácil deste módulo, e o que a câmera desta montagem já cometeu
    ("descartando quadro atrasado"): o ESP32 empurra 62,8 KB/s sem parar, e um
    cliente que só lê quando alguém pede entrega o som de segundos atrás - o
    robô responderia ao passado.

    Aqui: deixa 2s acumularem e confere que `gravar` NÃO devolve aquilo
    instantaneamente, e sim coleta o que chega a partir de agora.
    """
    esp = await ESP32Falso().iniciar()
    ouvidos = OuvidosESP32("127.0.0.1", esp.porta)
    await ouvidos.iniciar()
    try:
        await asyncio.sleep(2.0)  # acumula bastante áudio "velho"
        inicio = asyncio.get_running_loop().time()
        await ouvidos.gravar(0.6)
        gasto = asyncio.get_running_loop().time() - inicio
        # se devolvesse o buffer velho, voltaria em ~0s
        assert gasto >= 0.45, (
            f"devolveu audio velho instantaneamente ({gasto:.2f}s) - "
            f"o robo responderia ao passado"
        )
    finally:
        await ouvidos.encerrar()
        await esp.parar()


@pytest.mark.asyncio
async def test_esp32_ausente_levanta_ErroAudio_e_nao_derruba():
    """Contrato igual ao do microfone local: fonte ausente é `ErroAudio`, que
    o VoiceCore já sabe tratar (publica voice.audio_error e segue). O ESP32
    pode estar desligado, e isso não pode derrubar o robô (Cap 6 s.8)."""
    ouvidos = OuvidosESP32("127.0.0.1", 1)  # porta 1: ninguém escuta
    await ouvidos.iniciar()
    try:
        assert ouvidos.conectado is False
        with pytest.raises(ErroAudio):
            await ouvidos.gravar(0.2)
    finally:
        await ouvidos.encerrar()


@pytest.mark.asyncio
async def test_reconecta_quando_o_esp32_cai():
    """O ESP32 reinicia, o WiFi cai, o IP muda. Nenhuma dessas coisas pode
    deixar o robô surdo de forma permanente."""
    esp = await ESP32Falso().iniciar()
    esp.derrubar_na_conexao = True
    ouvidos = OuvidosESP32("127.0.0.1", esp.porta, intervalo_reconexao_s=0.2)
    await ouvidos.iniciar()
    try:
        await asyncio.sleep(0.6)
        assert esp.conexoes >= 2, "não tentou reconectar"
        # agora o ESP32 volta a se comportar
        esp.derrubar_na_conexao = False
        for _ in range(30):
            await asyncio.sleep(0.1)
            if ouvidos.conectado:
                break
        assert ouvidos.conectado is True, "não se recuperou quando a fonte voltou"
        audio = await ouvidos.gravar(0.3)
        assert len(audio) > 0
    finally:
        await ouvidos.encerrar()
        await esp.parar()


@pytest.mark.asyncio
async def test_remove_o_dc_antes_de_escolher_o_canal():
    """Um canal com offset constante parece "mais forte" sem ter som nenhum -
    foi assim que o mic da webcam enganou a seleção por qualidade em
    2026-07-18. O canal com DC alto e som zero não pode ganhar."""
    esp = await ESP32Falso(esquerdo_amp=0.0, direito_amp=0.3).iniciar()

    # canal esquerdo: constante em 0,7 (DC puro, nenhum som)
    async def atender_com_dc(leitor, escritor):
        try:
            while True:
                n = 1600
                esq = np.full(n, 0.7)
                t = np.arange(n) / 16000
                dir_ = 0.3 * np.sin(2 * np.pi * 440 * t)
                escritor.write(_quadros(esq, dir_))
                await escritor.drain()
                await asyncio.sleep(0.1)
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass

    esp.servidor.close()
    await esp.servidor.wait_closed()
    servidor = await asyncio.start_server(atender_com_dc, "127.0.0.1", 0)
    porta = servidor.sockets[0].getsockname()[1]

    ouvidos = OuvidosESP32("127.0.0.1", porta)
    await ouvidos.iniciar()
    try:
        await asyncio.sleep(0.4)
        audio = await ouvidos.gravar(0.4)
        # se tivesse escolhido o DC, o rms seria ~0 (após remover a média)
        rms = float(np.sqrt(np.mean(audio**2)))
        assert rms > 0.15, f"escolheu o canal de DC constante (rms={rms:.3f})"
    finally:
        await ouvidos.encerrar()
        servidor.close()
        await servidor.wait_closed()
