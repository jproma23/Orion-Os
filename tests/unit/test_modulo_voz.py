"""Testes do modulo de voz (EDR-0023).

Com nucleo dublê: exercita o ciclo de vida sem microfone, sem Whisper e sem
Piper instalados.
"""
import asyncio

import pytest

from orion.kernel.event_bus import EventBus
from orion.kernel.modulo import ModuloOrion
from orion.voice.modulo import ModuloVoz

CONFIG_VOZ = {
    "wake_word": "fofão",
    "whisper_model": "base",
    "whisper_model_ativacao": "base",
    "piper_voice_path": "data/piper_voices/pt_BR-faber-medium.onnx",
    "saida_audio_indice": 2,
    "microfones_candidatos_indices": [3, 4],
    "vad": {
        "habilitado": True,
        "fator_acima_do_ruido": 2.5,
        "rms_minimo": 0.003,
        "janelas_de_historico": 30,
    },
}


class NucleoVozFalso:
    def __init__(self, falhar: bool = False) -> None:
        self.falhar = falhar
        self.parou = False
        self.rodou = False

    async def executar(self) -> None:
        self.rodou = True
        if self.falhar:
            raise RuntimeError("microfone sumiu")
        while not self.parou:
            await asyncio.sleep(0.01)

    def parar(self) -> None:
        self.parou = True


async def _processar(texto: str) -> str:
    return f"eco: {texto}"


def test_cumpre_o_contrato():
    modulo = ModuloVoz(EventBus(), CONFIG_VOZ, _processar, nucleo=NucleoVozFalso())
    assert isinstance(modulo, ModuloOrion)
    assert modulo.nome == "voice"


@pytest.mark.asyncio
async def test_sobe_e_fica_saudavel():
    nucleo = NucleoVozFalso()
    modulo = ModuloVoz(EventBus(), CONFIG_VOZ, _processar, frase_saudacao=None, nucleo=nucleo)

    assert not modulo.esta_saudavel()
    await modulo.iniciar()
    await asyncio.sleep(0.05)

    assert nucleo.rodou
    assert modulo.esta_saudavel()
    await modulo.encerrar()


@pytest.mark.asyncio
async def test_encerrar_solta_o_microfone_e_e_idempotente():
    nucleo = NucleoVozFalso()
    modulo = ModuloVoz(EventBus(), CONFIG_VOZ, _processar, frase_saudacao=None, nucleo=nucleo)
    await modulo.iniciar()
    await asyncio.sleep(0.05)

    await modulo.encerrar()
    assert nucleo.parou
    assert not modulo.esta_saudavel()
    await modulo.encerrar()  # contrato: nunca levanta, mesmo repetido


@pytest.mark.asyncio
async def test_nucleo_que_morre_deixa_o_modulo_doente():
    """Microfone que some no meio nao pode passar por saudavel - e assim que
    o Watchdog percebe (Cap 6 s.8)."""
    modulo = ModuloVoz(
        EventBus(), CONFIG_VOZ, _processar, frase_saudacao=None, nucleo=NucleoVozFalso(falhar=True)
    )
    await modulo.iniciar()
    await asyncio.sleep(0.05)

    assert not modulo.esta_saudavel()
    await modulo.encerrar()


@pytest.mark.asyncio
async def test_saudacao_que_falha_nao_derruba_a_escuta():
    """Achado de 2026-07-24: erro transitorio no audio de SAIDA derrubava
    avatar, sentinela e chat juntos. Robo mudo ainda tem que ouvir."""

    class SinteseQuebrada:
        async def falar(self, texto: str) -> None:
            raise RuntimeError("placa de som ocupada")

    nucleo = NucleoVozFalso()
    modulo = ModuloVoz(EventBus(), CONFIG_VOZ, _processar, nucleo=nucleo)
    modulo._sintetizador = SinteseQuebrada()

    await modulo.iniciar()  # nao pode levantar
    await asyncio.sleep(0.05)

    assert modulo.esta_saudavel()  # a escuta continua de pe
    await modulo.encerrar()
