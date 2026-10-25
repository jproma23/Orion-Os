"""Testes da ponte serial entre o Event Bus e o orion_face (2026-09-12).

Sem porta real: substitui serial.Serial por um fake que so acumula bytes
escritos, mesmo padrao usado em test_avatar_server.py pro socket HTTP.
"""
import asyncio
import contextlib
import json

import numpy as np
import pytest
import pytest_asyncio
import serial as pyserial

from orion.display.ponte_serial import PonteSerial, TradutorRosto, empacotar_audio
from orion.kernel.event_bus import EventBus


class _SerialFake:
    def __init__(self):
        self.linhas: list[str] = []
        self.binario = bytearray()
        self.fechado = False

    def write(self, dados: bytes) -> int:
        # os quadros de audio nao sao texto (comecam com 0xFE) - guarda os dois
        # tipos separados, que e exatamente o que o firmware faz do outro lado
        try:
            self.linhas.append(dados.decode("utf-8"))
        except UnicodeDecodeError:
            self.binario.extend(dados)
        return len(dados)

    def close(self) -> None:
        self.fechado = True


@pytest.fixture
def serial_fake(monkeypatch):
    fake = _SerialFake()
    monkeypatch.setattr("orion.display.ponte_serial.serial.Serial", lambda *a, **kw: fake)
    return fake


@pytest_asyncio.fixture
async def bus_rodando():
    bus = EventBus()
    # publish() so enfileira - precisa do loop de despacho rodando pra
    # entregar de fato aos assinantes (mesmo padrao de test_avatar_server.py).
    tarefa = asyncio.create_task(bus.iniciar())
    yield bus
    bus.parar()
    tarefa.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarefa


@pytest.mark.asyncio
async def test_repassa_inicio_e_fim_da_fala(serial_fake, bus_rodando):
    ponte = PonteSerial(bus_rodando, porta="/dev/fake")
    await ponte.iniciar()

    await bus_rodando.publish("voice.response_started", {})
    await bus_rodando.publish("voice.response_finished", {})
    await asyncio.sleep(0.05)

    assert json.loads(serial_fake.linhas[0]) == {"evento": "voice.response_started"}
    assert json.loads(serial_fake.linhas[1]) == {"evento": "voice.response_finished"}

    await ponte.encerrar()
    assert serial_fake.fechado


@pytest.mark.asyncio
async def test_repassa_pan_tilt_com_campos(serial_fake, bus_rodando):
    ponte = PonteSerial(bus_rodando, porta="/dev/fake")
    await ponte.iniciar()

    await bus_rodando.publish("motion.pan_tilt", {"pan": 15.0, "tilt": -5.0})
    await asyncio.sleep(0.05)

    assert json.loads(serial_fake.linhas[0]) == {
        "evento": "motion.pan_tilt",
        "pan": 15.0,
        "tilt": -5.0,
    }

    await ponte.encerrar()


def _desempacotar(quadro: bytes) -> bytes:
    """Desfaz o enquadramento do jeito que o firmware desfaz (net.cpp)."""
    assert quadro[0] == 0xFE, "todo quadro comeca com a marca"
    corpo = bytearray()
    i = 1
    while i < len(quadro):
        if quadro[i] == 0xFD:  # escape: o proximo byte volta com XOR 0x20
            corpo.append(quadro[i + 1] ^ 0x20)
            i += 2
        else:
            corpo.append(quadro[i])
            i += 1
    tam = (corpo[0] << 8) | corpo[1]
    payload = bytes(corpo[2:])
    assert len(payload) == tam, "o tamanho anunciado tem que bater com o payload"
    return payload


@pytest.mark.asyncio
async def test_fala_vai_para_a_tela_reamostrada(serial_fake, bus_rodando):
    ponte = PonteSerial(bus_rodando, porta="/dev/fake")
    await ponte.iniciar()

    # 1 segundo de PCM a 22050Hz (a taxa nativa do Piper)
    audio = np.zeros(22050, dtype=np.int16)
    audio[::2] = 1000  # algo diferente de silencio puro
    await bus_rodando.publish("voice.audio_pcm", {"audio": audio, "taxa": 22050})
    await asyncio.sleep(1.5)  # o envio e no ritmo da fala: ~1s

    assert serial_fake.binario, "nada chegou na serial"
    assert serial_fake.binario[0] == 0xFE

    # Soma o payload de todos os quadros: tem que dar 1 segundo a 16kHz,
    # nao os 22050 originais - senao a voz sairia acelerada e aguda.
    total = 0
    restante = bytes(serial_fake.binario)
    while restante:
        proximo = restante.find(b"\xfe", 1)
        quadro, restante = (
            (restante, b"") if proximo < 0 else (restante[:proximo], restante[proximo:])
        )
        total += len(_desempacotar(quadro))
    assert total == pytest.approx(16000 * 2, rel=0.02)

    await ponte.encerrar()


@pytest.mark.asyncio
async def test_audio_nao_bloqueia_os_outros_eventos(serial_fake, bus_rodando):
    """O EventBus e serializado: se o envio da fala fosse `await` direto no
    assinante, o rosto pararia de receber pan/tilt durante a fala inteira."""
    ponte = PonteSerial(bus_rodando, porta="/dev/fake")
    await ponte.iniciar()

    audio = np.zeros(16000 * 3, dtype=np.int16)  # 3 segundos de fala
    await bus_rodando.publish("voice.audio_pcm", {"audio": audio, "taxa": 16000})
    await bus_rodando.publish("motion.pan_tilt", {"pan": 1.0, "tilt": 2.0})
    await asyncio.sleep(0.2)  # muito antes dos 3s de fala terminarem

    assert any("motion.pan_tilt" in linha for linha in serial_fake.linhas)

    await ponte.encerrar()


@pytest.mark.asyncio
async def test_mic_so_entrega_quadro_inteiro(serial_fake, bus_rodando):
    """Meio quadro no buffer desalinha os canais e vira ruido branco.

    Achado real (2026-09-17): `gravar()` limpa o buffer a qualquer instante,
    inclusive no meio de um quadro que esta chegando. Se o pedaco restante
    entrasse sozinho, ele poderia comecar no meio de uma amostra - e um byte
    fora de lugar transformava a fala em chiado de escala cheia (rms=0,57,
    pico=1,0, Whisper transcrevendo vazio).
    """
    ponte = PonteSerial(bus_rodando, porta="/dev/fake")
    await ponte.iniciar()

    # um quadro de audio: 4 amostras estereo, todas iguais e mansas
    pcm = np.full(8, 1000, dtype=np.int16).tobytes()
    quadro = empacotar_audio(pcm)

    # chega partido em dois pedacos, como a USB de fato entrega
    ponte._desempacotar(quadro[:5])
    assert not ponte._bruto, "quadro incompleto nao pode entrar no buffer"

    ponte._desempacotar(quadro[5:])
    assert bytes(ponte._bruto) == pcm, "quadro completo entra inteiro e igual"

    await ponte.encerrar()


@pytest.mark.asyncio
async def test_mic_ganho_preserva_a_distancia_entre_fala_e_silencio(
    serial_fake, bus_rodando
):
    """O ganho NAO pode achatar a diferenca entre fala e ruido.

    Achado real (2026-09-17): com ganho automatico o silencio era multiplicado
    por 8x e a fala por 3x, entao os 7,7x medidos entre uma coisa e outra
    viravam ~2x. O VAD so acorda o Whisper com som acima do ruido - resultado,
    o robo ficou surdo por causa do ajuste feito para ouvir melhor (1 unica
    transcricao em 6 minutos no ar). Ganho fixo mantem a razao intacta.
    """
    ponte = PonteSerial(bus_rodando, porta="/dev/fake")
    rng = np.random.default_rng(1)

    # niveis reais medidos neste microfone: fala rms~0,035, silencio rms~0,0045
    fala = (np.sin(np.linspace(0, 200, 3200)) * 0.035 * 32768).astype(np.int16)
    silencio = (rng.normal(0, 0.0045, 3200) * 32768).astype(np.int16)

    saida_fala = ponte._converter(np.repeat(fala, 2).tobytes())
    saida_silencio = ponte._converter(np.repeat(silencio, 2).tobytes())
    assert saida_fala.size == fala.size, "estereo intercalado vira mono"

    def _rms(x):
        return float(np.sqrt(np.mean(np.square(x))))

    razao_antes = _rms(fala / 32768) / _rms(silencio / 32768)
    razao_depois = _rms(saida_fala) / _rms(saida_silencio)
    assert razao_depois == pytest.approx(razao_antes, rel=0.05), (
        f"o ganho achatou a dinamica: {razao_antes:.1f}x virou {razao_depois:.1f}x"
    )

    assert 0.05 <= _rms(saida_fala) <= 0.25, "a fala ainda precisa chegar audivel"
    assert float(np.abs(saida_fala).max()) <= 1.0, "nada pode estourar"


@pytest.mark.asyncio
async def test_porta_ausente_nao_levanta(monkeypatch, bus_rodando):
    def _explode(*a, **kw):
        raise pyserial.SerialException("porta nao existe")

    monkeypatch.setattr("orion.display.ponte_serial.serial.Serial", _explode)

    ponte = PonteSerial(bus_rodando, porta="/dev/nao-existe")
    await ponte.iniciar()  # nao pode levantar

    await bus_rodando.publish("voice.response_started", {})
    await asyncio.sleep(0.05)  # tambem nao pode levantar aqui


def test_resolver_porta(monkeypatch, tmp_path):
    from orion.display.ponte_serial import resolver_porta

    fake_porta = tmp_path / "ttyACM0"
    fake_porta.touch()
    assert resolver_porta(str(fake_porta)) == str(fake_porta)


# ---------------------------------------------------------------------------
# Reacoes do rosto (2026-09-27)
# ---------------------------------------------------------------------------


class _Relogio:
    def __init__(self):
        self.agora = 1000.0

    def __call__(self):
        return self.agora


def test_rosto_pessoa_detectada_no_maximo_uma_vez_por_segundo():
    relogio = _Relogio()
    tradutor = TradutorRosto(relogio)
    assert tradutor.traduzir("vision.person_detected", {"classe": "person"}) == [{"tipo": "pessoa"}]
    relogio.agora += 0.5
    assert tradutor.traduzir("vision.person_detected", {"classe": "person"}) == []
    relogio.agora += 0.6
    assert tradutor.traduzir("vision.person_detected", {"classe": "person"}) == [{"tipo": "pessoa"}]


def test_rosto_reconhecer_alguem_faz_festa_uma_vez_por_pessoa():
    relogio = _Relogio()
    tradutor = TradutorRosto(relogio)
    festa = [{"tipo": "emocao", "nome": "muito_feliz", "valor": 2000}]
    assert tradutor.traduzir("vision.person_recognized", {"pessoa_id": 1, "nome": "Joao"}) == festa
    relogio.agora += 60
    assert tradutor.traduzir("vision.person_recognized", {"pessoa_id": 1, "nome": "Joao"}) == []
    # outra pessoa nao espera a primeira
    assert tradutor.traduzir("vision.person_recognized", {"pessoa_id": 2, "nome": "Mara"}) == festa
    relogio.agora += 300
    assert tradutor.traduzir("vision.person_recognized", {"pessoa_id": 1, "nome": "Joao"}) == festa


def test_rosto_pessoa_sumiu_libera_a_proxima_deteccao():
    relogio = _Relogio()
    tradutor = TradutorRosto(relogio)
    tradutor.traduzir("vision.person_detected", {})
    assert tradutor.traduzir("vision.person_lost", {}) == [{"tipo": "pessoa_sumiu"}]
    relogio.agora += 0.1
    assert tradutor.traduzir("vision.person_detected", {}) == [{"tipo": "pessoa"}]


def test_rosto_segue_os_estados_da_voz_so_nas_mudancas():
    tradutor = TradutorRosto(_Relogio())
    # a escuta de fundo (LISTENING) nao e "estou te ouvindo"
    assert tradutor.traduzir("voice.status", {"estado": "LISTENING"}) == []
    acordou = tradutor.traduzir("voice.status", {"estado": "WAKE_DETECTED"})
    assert acordou == [{"tipo": "ouvindo_inicio"}]
    assert tradutor.traduzir("voice.status", {"estado": "TRANSCRIBING"}) == []
    assert tradutor.traduzir("voice.status", {"estado": "THINKING"}) == [
        {"tipo": "ouvindo_fim"},
        {"tipo": "pensando_inicio"},
    ]
    assert tradutor.traduzir("voice.status", {"estado": "SPEAKING"}) == [{"tipo": "pensando_fim"}]
    assert tradutor.traduzir("voice.status", {"estado": "IDLE"}) == []


@pytest.mark.asyncio
async def test_ponte_manda_reacao_do_rosto_pela_serial(serial_fake, bus_rodando):
    ponte = PonteSerial(bus_rodando, porta="/dev/fake")
    await ponte.iniciar()

    await bus_rodando.publish("vision.person_lost", {"timestamp": 1.0})
    await bus_rodando.publish("voice.status", {"estado": "THINKING"})
    await asyncio.sleep(0.05)

    recebidas = [json.loads(linha) for linha in serial_fake.linhas]
    assert {"evento": "rosto", "tipo": "pessoa_sumiu"} in recebidas
    assert {"evento": "rosto", "tipo": "pensando_inicio"} in recebidas

    await ponte.encerrar()
