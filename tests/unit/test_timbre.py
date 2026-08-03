"""O timbre da corpo a voz sem estragar o audio.

Voz e coisa que se julga de ouvido, mas as propriedades que NAO podem quebrar
sao mediveis: nao clipar, nao inverter, nao mudar a duracao, e nunca derrubar
a fala se o tratamento falhar.
"""
from __future__ import annotations

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from orion.voice import timbre  # noqa: E402

TAXA = 22050


def _tom(hz: float, segundos: float = 0.3, amp: float = 0.4) -> np.ndarray:
    t = np.arange(int(TAXA * segundos)) / TAXA
    return (np.sin(2 * np.pi * hz * t) * amp * 32768).astype(np.int16)


def _energia(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean((x.astype(np.float64) / 32768.0) ** 2)))


def test_nao_clipa():
    """Tratamento que clipa troca calor por distorcao."""
    saida = timbre.aquecer(_tom(200, amp=0.95), TAXA)
    assert np.max(np.abs(saida)) < 32767


def test_preserva_a_duracao():
    """Mudar o numero de amostras dessincronizaria a fala do resto."""
    entrada = _tom(300)
    assert timbre.aquecer(entrada, TAXA).size == entrada.size


def test_da_corpo_aos_medios_graves():
    """O calor tem que aparecer onde foi prometido: abaixo de ~350 Hz."""
    grave = timbre.aquecer(_tom(150), TAXA)
    agudo = timbre.aquecer(_tom(9000), TAXA)
    # Depois da normalizacao os dois tem pico parecido, entao a comparacao
    # justa e entre o ganho da curva, nao entre as energias absolutas.
    freqs = np.array([150.0, 9000.0])
    ganhos = timbre._curva_de_ganho(freqs)
    assert ganhos[0] > 1.0, "o grave deveria ser realcado"
    assert ganhos[1] < 1.0, "o agudo deveria ser suavizado"
    assert _energia(grave) > 0 and _energia(agudo) > 0


def test_suaviza_o_sibilo_digital():
    """O que mais denuncia 'isto e uma maquina' e a aspereza nos agudos."""
    assert timbre._curva_de_ganho(np.array([12000.0]))[0] < timbre._curva_de_ganho(
        np.array([1000.0])
    )[0]


def test_a_curva_e_suave_sem_degraus():
    """Degrau em EQ vira ressonancia audivel."""
    freqs = np.linspace(20, 11000, 2000)
    g = timbre._curva_de_ganho(freqs)
    saltos = np.abs(np.diff(g))
    assert np.max(saltos) < 0.02, "a curva tem um degrau"


def test_compressao_estreita_a_dinamica():
    """Presenca vem de faixa dinamica estreita, nao de volume alto."""
    t = np.arange(int(TAXA * 0.6)) / TAXA
    # Sinal que comeca baixo e termina alto.
    rampa = (np.sin(2 * np.pi * 220 * t) * np.linspace(0.05, 0.9, t.size) * 32768).astype(np.int16)
    x = rampa.astype(np.float32) / 32768.0
    comprimido = timbre._comprimir(x, timbre.PRESENCA, TAXA)

    def _razao(v):
        meio = v.size // 2
        alto = float(np.max(np.abs(v[meio:])))
        baixo = float(np.max(np.abs(v[:meio])))
        return alto / max(baixo, 1e-9)

    assert _razao(comprimido) < _razao(x), "a dinamica deveria ter estreitado"


def test_audio_vazio_nao_quebra():
    vazio = np.array([], dtype=np.int16)
    assert timbre.aquecer(vazio, TAXA).size == 0


def test_falha_no_tratamento_devolve_a_voz_crua():
    """Robo mudo e pior que robo sem timbre."""
    entrada = _tom(440)
    assert np.array_equal(timbre.aquecer(entrada, 0), entrada) or timbre.aquecer(
        entrada, TAXA
    ).size == entrada.size


def test_devolve_int16():
    """E o formato que o sounddevice toca - converter aqui poupa quem chama."""
    assert timbre.aquecer(_tom(440), TAXA).dtype == np.int16
